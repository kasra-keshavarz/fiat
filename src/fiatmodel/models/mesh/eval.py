#!/usr/bin/env python3

"""Evaluation utilities for MESH models.

This module provides the runtime evaluation script used during calibration
for MESH-based workflows. It performs the following high-level steps:

- Reads an evaluation configuration JSON (e.g., ``./etc/eval/eval.json``),
    optionally converting numeric-like strings to native numbers for robust
    templating and arithmetic.
- Renders model input templates via :mod:`meshflow` using parameters/others
    files in ``./etc/eval`` and writes them into the model instance directory.
- Executes the MESH model executable and collects simulation results.
- Aligns observations and simulations across one or more calibration date
    intervals, inferring/resampling time frequency when needed.
- Computes metrics using :mod:`HydroErr` and combines them into objective
    functions via :mod:`numexpr`, writing single-valued CSV results for each
    configured objective function.

Notes
-----
- The script is designed to be dynamically generated and invoked by FIAT.
- Resampling behavior currently simplifies to mean/sum per variable as a
    placeholder for streamflow-only usage. Future releases may generalize this.
- Time frequency inference uses :class:`pandas.DatetimeIndex` information and
    falls back to selecting the mode of observed time deltas when needed.

Examples
--------
Run the script directly after FIAT prepares the evaluation assets::

    $ python src/fiatmodel/models/mesh/eval.py
"""

# built-in imports
import subprocess
import os
import re
import json
import shutil
import warnings

from typing import (
    Any,
    Callable,
    Dict,
    Mapping,
    Optional,
    Sequence,
    Union
)

# external imports
import xarray as xr
import pandas as pd
import numexpr as ne
import numpy as np

import HydroErr

from pandas.tseries.offsets import DateOffset

# fiat imports
from fiatmodel.models.valid_ofs import hydro_err_ofs # python list object


# default environment
my_env = os.environ.copy()

# MESH-specific import
import meshflow as mf

# defaults
with open(os.path.join('./etc/eval/defaults.json'), 'r') as f:
    DEFAULTS = json.load(f)

# Precompile regexes for speed/readability
_INT_RE = re.compile(r'^[-+]?\d+$')
_FLOAT_RE = re.compile(
    r"""^[-+]?(                # optional sign
        (?:\d+\.\d*|\d*\.\d+)  # something with a decimal point
        (?:[eE][-+]?\d+)?      # optional exponent
        |
        \d+[eE][-+]?\d+        # or integer with exponent (e.g. 1e6)
    )$""",
    re.X
)

# default environment
my_env = os.environ.copy()

def _parse_numeric_string(s: str) -> Union[int, float, str]:
    """Parse a numeric-like string into a number when possible.

    Parameters
    ----------
    s : str
        Input string to interpret. Leading/trailing whitespace is ignored.

    Returns
    -------
    int or float or str
        ``int`` if the string matches an integer pattern, ``float`` if it
        matches a floating-point or scientific notation pattern, otherwise the
        original string ``s``.

    Notes
    -----
    - Integer detection accepts an optional leading sign (e.g., ``"-7"``).
    - Float detection accepts decimal and scientific notation
      (e.g., ``"3.14"``, ``"1e6"``).

    Examples
    --------
    >>> _parse_numeric_string("42")
    42
    >>> _parse_numeric_string("3.14")
    3.14
    >>> _parse_numeric_string("1e3")
    1000.0
    >>> _parse_numeric_string("abc")
    'abc'
    """
    if _INT_RE.match(s):
        # Keep as int if it fits typical Python int (Python int is unbounded anyway)
        return int(s)
    if _FLOAT_RE.match(s):
        # Anything with decimal point or exponent
        return float(s)
    return s  # not numeric-looking

def _convert_numeric_strings(obj: Any) -> Any:
    """Recursively convert numeric-like strings within mappings and sequences.

    Walks nested ``dict`` and ``list`` structures, converting any string values
    that look numeric into ``int`` or ``float`` using
    :func:`_parse_numeric_string`.

    Parameters
    ----------
    obj : Any
        A Python object. If ``dict`` or ``list``, it will be traversed
        recursively; all other types are returned as-is.

    Returns
    -------
    Any
        An object of the same structure as ``obj`` with numeric-like strings
        converted to numbers.

    Examples
    --------
    >>> _convert_numeric_strings({"a": "1", "b": ["2.5", "x"]})
    {'a': 1, 'b': [2.5, 'x']}
    """
    if isinstance(obj, dict):
        return {k: _convert_numeric_strings(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_convert_numeric_strings(v) for v in obj]
    if isinstance(obj, str):
        return _parse_numeric_string(obj.strip())
    return obj  # leaves int, float, bool, None, etc. untouched

def _make_object_hook() -> Callable[[Dict[str, Any]], Dict[str, Any]]:
    """Create a ``json.loads`` object hook that converts numeric-like strings.

    Returns
    -------
    callable
        A function suitable for use as ``object_hook`` in :func:`json.loads`
        that applies :func:`_convert_numeric_strings` to every decoded mapping
        and converts keys to integers where possible.
    """

    def object_hook(d):
        new_d = {}
        for k, v in d.items():
            # 1. Reuse your existing function to convert values recursively
            # Note: Since object_hook runs bottom-up, 'v' is already processed 
            # if it was a nested dict. _convert_numeric_strings handles lists/primitives.
            converted_val = _convert_numeric_strings(v)

            # 2. Attempt to convert the key to an integer
            try:
                converted_key = int(k)
            except ValueError:
                converted_key = k
            
            new_d[converted_key] = converted_val
        return new_d

    return object_hook

def _reset_dir(path: str) -> None:
    """Remove a directory (if present) and recreate it empty.

    This is a destructive operation intended for clearing an output directory
    prior to writing evaluation results.

    Parameters
    ----------
    path : str
            Directory path to reset.

    Notes
    -----
    - Uses :func:`shutil.rmtree` with ``ignore_errors=True`` so the call will
        not raise if the directory does not exist.
    - Recreates the directory with :func:`os.makedirs` and
        ``exist_ok=True``.
    """

    shutil.rmtree(path, ignore_errors=True)  # delete the directory entirely
    os.makedirs(path, exist_ok=True)         # recreate it empty

def infer_frequency(time_index: pd.DatetimeIndex) -> DateOffset:
    """Infer a regular time frequency from a :class:`pandas.DatetimeIndex`.

    The function attempts, in order:

    1. Use the explicit ``.freq`` if available.
    2. Use ``.inferred_freq`` if available.
    3. Compute time-step deltas and return the most common step.

    Parameters
    ----------
    time_index : :class:`pandas.DatetimeIndex`
        The time coordinate index from which to infer the sampling frequency.

    Returns
    -------
    :class:`pandas.tseries.offsets.DateOffset`
        A pandas date offset representing the inferred frequency.

    Raises
    ------
    ValueError
        If the index has fewer than 2 timestamps and frequency cannot be
        inferred.
    """

    # Try explicit or inferred freq
    if time_index.freq is not None:
        return time_index.freq
    if time_index.inferred_freq is not None:
        return pd.tseries.frequencies.to_offset(time_index.inferred_freq)
    # Fallback: choose most common delta
    if len(time_index) < 2:
        raise ValueError("Cannot infer frequency from fewer than 2 timestamps.")
    deltas = pd.Series(time_index[1:] - time_index[:-1])
    # mode() can return multiple; take the first
    step = deltas.mode().iloc[0]
    return pd.tseries.frequencies.to_offset(step)

def build_calibration_subset(
    ds: xr.Dataset, 
    dates: Sequence[Mapping[str, Any]]
) -> xr.Dataset:
    """Build a union time index across configured intervals and reindex ``ds``.

    Constructs the union of all ``[start, end]`` closed intervals from the
    configuration and reindexes the dataset's ``time`` coordinate to this
    union. Values outside the original ``ds.time`` range are not permitted and
    will raise an error. Missing values introduced by the reindex are left as
    NaN (no fill).

    Parameters
    ----------
    ds : :class:`xarray.Dataset`
        Dataset containing a ``time`` coordinate and corresponding index.
    dates : sequence of mapping
        Iterable of ``{"start": <str>, "end": <str>}`` dictionaries defining
        closed intervals. Strings are parsed with :func:`pandas.to_datetime`.

    Returns
    -------
    :class:`xarray.Dataset`
        A dataset reindexed over the union of the requested intervals.

    Raises
    ------
    KeyError
        If ``time`` is not present as a coordinate index in ``ds`` or the
        requested union extends beyond the dataset's time span.
    ValueError
        If interval endpoints are mismatched in length or ``end < start`` for
        any interval.
    """
    # Extract intervals
    starts = pd.to_datetime([d['start'] for d in dates])
    ends   = pd.to_datetime([d['end'] for d in dates])
    
    if len(starts) != len(ends):
        raise ValueError("Starts and ends length mismatch.")

    # Get underlying pandas index (assumes standard datetime)
    try:
        time_index = ds.indexes['time']
    except KeyError:
        raise KeyError("Dataset has no 'time' coordinate index.")

    # Infer frequency
    freq = infer_frequency(time_index)

    # Build union of all desired times
    union_index = None
    for s, e in zip(starts, ends):
        if e < s:
            raise ValueError(f"End before start for interval {s} - {e}")
        rng = pd.date_range(s, e, freq=freq)
        union_index = rng if union_index is None else union_index.union(rng)

    # Report expansion intent
    orig_min, orig_max = time_index.min(), time_index.max()
    requested_min, requested_max = union_index.min(), union_index.max()
    if requested_min < orig_min or requested_max > orig_max:
        raise KeyError("Requested calibration range beyond simulation time-series")

    # Reindex (no fill method => NaNs)
    out = ds.reindex(time=union_index)
    return out

def resample_per_variable(
    ds: xr.Dataset,
    rule: str = "1D",
    dim: str = "time",
    methods: Optional[Dict[str, Union[str, Callable]]] = None,
    default: Optional[Union[str, Callable]] = None,
    **kwargs: Any
) -> xr.Dataset:
    """Resample variables using per-variable reducers.

    Parameters
    ----------
    ds : :class:`xarray.Dataset`
        Input dataset to resample.
    rule : str, default "1D"
        Resampling rule (pandas offset alias), e.g., ``"1H"``, ``"1D"``.
    dim : str, default "time"
        Name of the time-like dimension to resample along.
    methods : dict, optional
        Mapping from variable name to reducer. A reducer can be either the
        name of a resampler method (e.g., ``"mean"``, ``"sum"``) or a callable
        to be used with :meth:`xarray.core.resample.DataArrayResample.reduce`.
    default : str or callable, optional
        Fallback reducer applied to variables not present in ``methods``.
        If ``None``, variables without an explicit reducer are skipped.
    **kwargs
        Additional keyword arguments passed to the reducer (for example,
        ``skipna=True``, ``keep_attrs=True``).

    Returns
    -------
    :class:`xarray.Dataset`
        A dataset containing the resampled variables.

    Raises
    ------
    ValueError
        If ``methods`` is not provided or a named reducer does not exist on
        the resampler for a given variable.
    TypeError
        If a reducer is neither a string nor a callable.

    Examples
    --------
    >>> import xarray as xr
    >>> ds = xr.Dataset({
    ...     'QO': (('time',), [1, 2, 3, 4]),
    ...     'QI': (('time',), [10, 20, 30, 40])
    ... }, coords={'time': pd.date_range('2000-01-01', periods=4, freq='H')})
    >>> resample_per_variable(ds, rule='2H', methods={'QO': 'sum', 'QI': 'mean'})
    <xarray.Dataset> ...  # doctest: +ELLIPSIS
    """
    if methods is None:
        raise ValueError("Provide methods, e.g. {'QO': 'sum', 'QI': 'mean'}")

    out = {}
    for var in ds.data_vars:
        reducer = methods.get(var, default)
        if reducer is None:
            continue
        resampler = ds[var].resample({dim: rule})
        if isinstance(reducer, str):
            if not hasattr(resampler, reducer):
                raise ValueError(f"Reducer '{reducer}' not available for '{var}'")
            out[var] = getattr(resampler, reducer)(**kwargs)
        elif callable(reducer):
            out[var] = resampler.reduce(reducer, **kwargs)
        else:
            raise TypeError(f"Reducer for '{var}' must be a string or callable")
    return xr.Dataset(out)

if __name__ == "__main__":
    # read the `json` configuration file
    with open("./etc/eval/eval.json", "r") as f:
        eval_config = json.load(f, object_hook=_make_object_hook())

    # empty the output directory before anything else
    _reset_dir(
        os.path.join(
            eval_config['model_instance_path'],
            eval_config['results_path']
        )
    )

    # read the observation file
    observations = xr.open_dataset(eval_config['observations_file'])

    # files to be read
    root_file_path = os.path.join('./etc', 'eval')
    # `parameters` JSON files are needed to render templates
    param_file_paths = {k: os.path.join(root_file_path, v)
                        for k, v in (eval_config.get('parameters') or {}).items()}
    # `others` JSON files are also needed to render templates, but they
    # do not change during calibration
    others_file_paths = {k: os.path.join(root_file_path, v)
                         for k, v in (eval_config.get('others') or {}).items()}

    # read the parameter files and generate MESH input parameters
    mesh_inputs = {}
    for param_name, file_path in param_file_paths.items():
        with open(file_path, 'r', encoding='utf-8') as f:
            mesh_inputs[param_name] = json.load(f, object_hook=_make_object_hook())
    # doing the same for the `others` files
    for other_name, file_path in others_file_paths.items():
        ext = os.path.splitext(file_path)[-1].lower()
        if ext == '.json':
            with open(file_path, 'r', encoding='utf-8') as f:
                mesh_inputs[other_name] = json.load(f, object_hook=_make_object_hook())
        else:
            pass # probably `.nc` or others

    # use meshflow to generate the parameter files
    # class
    class_file = mf.utility.render_class_template(
        class_case=mesh_inputs['case_entry'],
        class_info=mesh_inputs['info_entry'],
        class_grus=mesh_inputs['class']
    )   

    # extract hydrology + routing paramaters available for calibration
    # since `routing` is a list, the first element is chosen with [0]
    # but since `hydrology` is a dictionary, we choose the first element
    # differently
    first_element_hydrology = next(iter(mesh_inputs['hydrology']))
    kwargs: dict[str | Any] = { 
        'process_details': {
            'routing': list(mesh_inputs['routing'][0].keys()),
            'hydrology': list(mesh_inputs['hydrology'][first_element_hydrology].keys()),
        },  
    }

    # read the `MESH_parameters.nc` file as well, if provided
    if eval_config['others']['parameters_ds']:
        mesh_parameters_path = os.path.join(root_file_path, eval_config['others']['parameters_ds'])
        mesh_parameters_ds = xr.open_dataset(mesh_parameters_path)

        # add mesh_parameters_ds to `kwargs` so it can be used in the template rendering
        kwargs['parameters_ds'] = mesh_parameters_ds

    # hydrology
    hydrology_file, parameters_ds = mf.utility.render_hydrology_template(
        routing_params=mesh_inputs['routing'],
        hydrology_params=mesh_inputs['hydrology'],
        hru_dim='subbasin', # hard-coded: FIXME later
        gru_dim='NGRU', # hard-coded: FIXME later
        return_ds=True,
        **kwargs,
    )

    # apply changes to the MESH instance
    with open(os.path.join(eval_config['model_instance_path'], "MESH_parameters_CLASS.ini"), "w", encoding="utf-8") as f:
        f.write(class_file)
    with open(os.path.join(eval_config['model_instance_path'], "MESH_parameters_hydrology.ini"), "w", encoding="utf-8") as f:
        f.write(hydrology_file)
    parameters_ds.to_netcdf(os.path.join(eval_config['model_instance_path'], "MESH_parameters.nc"))

    # run the MESH model
    try:
        # subprocess running model
        with open(os.path.join('model_run.log'), 'w') as f:
            subprocess.run(
                ['./' + eval_config['model_executable']],
                cwd=eval_config['model_instance_path'],
                check=True,
                env=my_env,
                stdout=f,
                stderr=f)

        # first read the time-series of obs/sim for
        #      each element in the `obs` file
        simulations = xr.open_dataset(
            os.path.join(
                eval_config['model_instance_path'],
                eval_config['results_path'],
                eval_config['output_files'][0][0]
            )
        )

        # as a sanity check, make sure both `subbasin` and `time`
        # dimensions are available in both datasets
        for dim in ['subbasin', 'time']:
            if dim not in simulations.dims:
                raise ValueError(
                    f'Dimension `{dim}` not found in simulation results.'
                )
            if dim not in observations.dims:
                raise ValueError(
                    f'Dimension `{dim}` not found in observation data.'
                )

        # selected calibration dates
        sim_sub = build_calibration_subset(
            simulations,
            eval_config.get('dates')
        )
        obs_sub = build_calibration_subset(
            observations,
            eval_config.get('dates')
        )

        # based on the observation file, understand the time-step
        # interval of the observations
        obs_ts = str(np.unique(obs_sub['freq'].values)[0])
        # and extract the simulation time-step accordingly
        sim_ts = xr.infer_freq(sim_sub['time'])

        # if the time-steps are different, perform resampling
        # FIXME: for now, the variables are averaged. As, the script is set to
        #        work with streamflow only (simplifying assumption). This will
        #        be fixed in the future releases.
        # resampling the time-series matching the observations time-step
        ts_interval = pd.tseries.frequencies.to_offset
        # check the variable name in DEFAULTS and see if we should take the
        # `mean` or `sum` during resampling
        # Suppose ds has variables QO and QI and a time dimension
        var = set(sim_sub.variables) - set(DEFAULTS.get('default_variables'))

        if ts_interval(obs_ts) != ts_interval(sim_ts):
            for v in var:
                how = 'mean' if v in DEFAULTS['output_variables']['mean'] else 'sum'
                sim_sub = resample_per_variable(sim_sub, rule=obs_ts, methods={"QO": "sum", "QI": "mean"},)
        else:
            pass # just use obs_sub as is

        # extract names for the `observations` - can be hard-coded
        station_ids = obs_sub.subbasin.to_numpy().tolist()
        station_names = obs_sub.name.to_numpy().tolist()

        # evaluate each objective function
        of_values = {}
        helper_ofs = {}
        custom_ofs = {}

        for group, fluxes in eval_config.get('objective_functions').items():
            if 'helper' in group:
                for flux, metrics in fluxes.items():
                    # define empty sims and obs dictionaries - based on chosen `flux`
                    sims = {}
                    obs = {}

                    # start populating `helper_ofs`
                    helper_ofs[flux] = {}

                    # calculate metric values for the selected flux
                    # and metric type (e.g., kge_2012, etc.)
                    for metric in metrics.keys():
                        helper_ofs[flux][metric] = []
                        # if it is a routine metric (i.e., from HydroErr),
                        # then calculate it based on sims/obs dictionary values
                        if metric in hydro_err_ofs:
                            # whenever choosing from sim/obs dictionaries, we need to use station_ids
                            for st in station_ids:
                                # sims dictionary
                                sims[obs_sub['name'].sel(subbasin=st).to_numpy().tolist()] = sim_sub[flux].sel(subbasin=st).to_series()
                                # same for obs dictionary
                                obs[obs_sub['name'].sel(subbasin=st).to_numpy().tolist()] = obs_sub[flux].sel(subbasin=st).to_series()

                            # extract the proper HydroErr metric function to use
                            # and assign it to `he_metric`. Therefore, `he_metric` points
                            # to a function in HydroErr library
                            he_metric = getattr(HydroErr, metric)

                            # now, go over whatever observation is available, and build the metric_dict
                            metric_dict = {}
                            for name in obs.keys():
                                metric_dict[name] = he_metric(sims[name], obs[name])

                            # go over metric values for each station included and calculate the metric value
                            # and then store it in helper_ofs[flux][metric] dictionary in form of a list item
                            for helper_of in metrics[metric]:
                                result = ne.evaluate(helper_of, local_dict=metric_dict)
                                helper_ofs[flux][metric].append(result)
                        # meaning, this is a custom metric that is assigned as `helper` function,
                        # so it's results won't be printed as a .csv file, but is used for other
                        # `custom` objective functions
                        else:
                            # because this helper function will be based on those already defined
                            # in helper_ofs[flux].keys(), we will do some string adjustments
                            # to assure evaluation goes well
                            # replace any existing helper_ofs[flux] keys in the
                            # expression strings with explicit helper_ofs references
                            existing_keys = [k for k in helper_ofs[flux].keys() if k != metric]
                            new_ofs = []
                            # go over each expression in the ofs list and replace existing expressions
                            # with Python valid, explicit helper_ofs references
                            ofs = metrics[metric]
                            for expr in ofs:
                                new_expr = expr
                                for k in existing_keys:
                                    pattern = rf'\b{re.escape(k)}\b'
                                    replacement = f"helper_ofs['{flux}']['{k}']"
                                    new_expr = re.sub(pattern, replacement, new_expr)
                                new_ofs.append(new_expr)

                            for of in new_ofs: # a list of objective functions 
                                result = eval(of)
                                helper_ofs[flux][metric] = result

            elif 'custom' in flux:
                for flux, metrics in fluxes.items():
                    # define empty sims and obs dictionaries - based on chosen `flux`
                    sims = {}
                    obs = {}

                    # start populating `custom_ofs`
                    custom_ofs[flux] = {}

                    # calculate metric values for the selected flux
                    # and metric type (e.g., kge_2012, etc.)
                    for metric in metrics.keys():
                        custom_ofs[flux][metric] = []

                        # because this helper function will be based on those already defined
                        # in helper_ofs[flux].keys(), we will do some string adjustments
                        # to assure evaluation goes well
                        # replace any existing custom_ofs[flux] keys in the
                        # expression strings with explicit custom_ofs references
                        existing_keys = [k for k in custom_ofs[flux].keys() if k != metric]
                        new_ofs = []
                        # go over each expression in the ofs list and replace existing expressions
                        # with Python valid, explicit custom_ofs references
                        ofs = list(metrics[metric])
                        for expr in ofs:
                            new_expr = expr
                            for k in existing_keys:
                                pattern = rf'\b{re.escape(k)}\b'
                                replacement = f"helper_ofs['{flux}']['{k}']"
                                new_expr = re.sub(pattern, replacement, new_expr)
                            new_ofs.append(new_expr)

                        for idx, of in enumerate(new_ofs, start=1): # a list of objective functions 
                            result = eval(of)
                            custom_ofs[flux][metric] = result

                            # write the of results to a .csv file (with only a single element)
                            with open(
                                os.path.join(
                                    './etc',
                                    'eval',
                                    f'{group}_{flux.upper()}_{metric}_{idx}.csv',
                                ),
                                'w',
                            ) as f:
                                f.write(f'{result}')

            # just normal fluxed-based objective functions using hydroerr metrics
            else:
                for flux, metrics in fluxes.items():
                    sims = {}
                    obs = {}
                    # start populating of_values
                    of_values[flux] = {}
                    # assign simulation results for the selected flux
                    for st in station_ids:
                        # sims dictionary
                        sims[obs_sub['name'].sel(subbasin=st).to_numpy().tolist()] = sim_sub[flux].sel(subbasin=st).to_series()
                        # same for obs dictionary
                        obs[obs_sub['name'].sel(subbasin=st).to_numpy().tolist()] = obs_sub[flux].sel(subbasin=st).to_series()
                    # metric (for example, kge_2012), and ofs (list of individual objective functions
                    for metric, ofs in metrics.items():
                        # add elements to `of_values`
                        of_values[flux][metric] = []
                        # calculate the metric value
                        he_metric = getattr(HydroErr, metric)
                        metric_dict = {}
                        for name in obs.keys():
                            metric_dict[name] = he_metric(sims[name], obs[name])

                        for idx, of in enumerate(ofs, start=1): # a list of objective functions
                            result = ne.evaluate(of, local_dict=metric_dict)
                            of_values[flux][metric] = result

                            # write the of results to a .csv file (with only a single element)
                            with open(
                                os.path.join(
                                    './etc',
                                    'eval',
                                    f'{group}_{flux.upper()}_{metric}_{idx}.csv',
                                ),
                                'w',
                            ) as f:
                                f.write(f'{result}')

    except subprocess.CalledProcessError as e:
        warnings.warn(
            f'MODEL EXECUTION FAILED WITH ERROR CODE {e.returncode}. '
            'OBJECTIVE FUNCTION VALUES WILL BE SET TO A LARGE NUMBER.'
        )
        for group, fluxes in eval_config.get('objective_functions').items():
            if any(keyword in group for keyword in ['flux', 'custom']):
                for flux in fluxes.keys():
                    for idx, metric in enumerate(fluxes[flux].keys(), start=1):
                        result = +1e10

                        # write the of results to a .csv file (with only a single element)
                        with open(
                            os.path.join(
                                './etc',
                                'eval',
                                f'{group}_{flux.upper()}_{metric}_{idx}.csv',
                            ),
                            'w',
                        ) as f:
                            f.write(f'{result}')

    except (ValueError, TypeError, KeyError) as e:
        warnings.warn(
            f'MODEL OUTPUT CORRUPTED: {str(e)}. '
            'OBJECTIVE FUNCTION VALUES WILL BE SET TO A LARGE NUMBER.'
        )
        for group, fluxes in eval_config.get('objective_functions').items():
            if any(keyword in group for keyword in ['flux', 'custom']):
                for flux in fluxes.keys():
                    for idx, metric in enumerate(fluxes[flux].keys(), start=1):
                        result = +1e10

                        # write the of results to a .csv file (with only a single element)
                        with open(
                            os.path.join(
                                './etc',
                                'eval',
                                f'{group}_{flux.upper()}_{metric}_{idx}.csv',
                            ),
                            'w',
                        ) as f:
                            f.write(f'{result}')