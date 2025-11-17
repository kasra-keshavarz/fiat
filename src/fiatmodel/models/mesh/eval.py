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
        that applies :func:`_convert_numeric_strings` to every decoded mapping.
    """

    def object_hook(d):
        for k, v in d.items():
            d[k] = _convert_numeric_strings(v)  # reuse earlier function
        return d
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
        with open(file_path, 'r', encoding='utf-8') as f:
            mesh_inputs[other_name] = json.load(f, object_hook=_make_object_hook())

    # use meshflow to generate the parameter files
    # class
    class_file = mf.utility.render_class_template(
        class_case=mesh_inputs['case_entry'],
        class_info=mesh_inputs['info_entry'],
        class_grus=mesh_inputs['class']
    )
    # hydrology
    hydrology_file = mf.utility.render_hydrology_template(
        routing_params=mesh_inputs['routing'],
        hydrology_params=mesh_inputs['hydrology'],
    )
    # apply changes to the MESH instance
    with open(os.path.join(eval_config['model_instance_path'], "MESH_parameters_CLASS.ini"), "w", encoding="utf-8") as f:
        f.write(class_file)
    with open(os.path.join(eval_config['model_instance_path'], "MESH_parameters_hydrology.ini"), "w", encoding="utf-8") as f:
        f.write(hydrology_file)

    # run the MESH model
    try:
        # subprocess running model
        subprocess.run(
            ['./' + eval_config['model_executable']],
            cwd=eval_config['model_instance_path'],
            check=True,
            env=my_env)

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

        for flux, metrics in eval_config.get('objective_functions').items():
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
                            f'{flux.upper()}_{metric}_{idx}.csv',
                        ),
                        'w',
                    ) as f:
                        f.write(f'{result}')

    except subprocess.CalledProcessError as e:
        warnings.warn(
            f'MODEL EXECUTION FAILED WITH ERROR CODE {e.returncode}. '
            'OBJECTIVE FUNCTION VALUES WILL BE SET TO A LARGE NUMBER.'
        )
        for flux, metrics in eval_config.get('objective_functions').items():
            for metric, ofs in metrics.items():
                for idx, of in enumerate(ofs, start=1): # a list of objective functions
                    # FIXME: Only coding for OSTRICH now, since Ostrich is always
                    #        dealing with a minimization problem, reporting a large
                    #        value for failed runs.
                    result = +1e10

                    # write the of results to a .csv file (with only a single element)
                    with open(
                        os.path.join(
                            './etc',
                            'eval',
                            f'{flux.upper()}_{metric}_{idx}.csv',
                        ),
                        'w',
                    ) as f:
                        f.write(f'{result}')

    except (ValueError, TypeError, KeyError) as e:
        warnings.warn(
            f'MODEL OUTPUT CORRUPTED: {str(e)}. '
            'OBJECTIVE FUNCTION VALUES WILL BE SET TO A LARGE NUMBER.'
        )
        for flux, metrics in eval_config.get('objective_functions').items():
            for metric, ofs in metrics.items():
                for idx, of in enumerate(ofs, start=1): # a list of objective functions
                    # FIXME: Only coding for OSTRICH now, since Ostrich is always
                    #        dealing with a minimization problem, reporting a large
                    #        value for failed runs.
                    result = +1e10

                    # write the of results to a .csv file (with only a single element)
                    with open(
                        os.path.join(
                            './etc',
                            'eval',
                            f'{flux.upper()}_{metric}_{idx}.csv',
                        ),
                        'w',
                    ) as f:
                        f.write(f'{result}')
