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
    intervals: the requested period is extracted from the simulation at its
    native time grid, aggregated to the observation time resolution, and the
    observations are then aligned onto the same time axis.
- Computes metrics using :mod:`HydroErr` and combines them into objective
    functions via :mod:`numexpr`, writing single-valued CSV results for each
    configured objective function.

Notes
-----
- The script is designed to be dynamically generated and invoked by FIAT.
- Resampling aggregation (e.g., mean vs. sum) is controlled per variable
    via the ``output_variables`` section of :file:`defaults.json`. Variables
    not listed there are resampled with ``mean`` and a warning is issued.
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
import sys
import textwrap
import warnings

from typing import (
    Any,
    Callable,
    Dict,
    Mapping,
    Optional,
    Sequence,
    Tuple,
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

def validate_dataset_dims(ds: xr.Dataset, label: str) -> None:
    """Sanity-check required dimensions and the ``time`` index of a dataset.

    Parameters
    ----------
    ds : xarray.Dataset
        Dataset to validate.
    label : str
        Human-readable description of the dataset used in error messages.

    Raises
    ------
    ValueError
        If the ``subbasin`` or ``time`` dimensions are missing, ``time``
        is not an index coordinate, or the ``time`` index contains
        duplicate timestamps.
    """
    for dim in ['subbasin', 'time']:
        if dim not in ds.dims:
            raise ValueError(f"Dimension `{dim}` not found in {label}.")
    if 'time' not in ds.indexes:
        raise ValueError(f"`time` is not an index coordinate in {label}.")

    time_index = ds.indexes['time']
    if not time_index.is_unique:
        n_dup = time_index.size - time_index.nunique()
        duplicates = time_index[time_index.duplicated(keep='first')]
        raise ValueError(
            f"`time` index in {label} has {n_dup} duplicate timestamp(s) of "
            f"{time_index.size} (e.g. {duplicates[:3].tolist()}). Ensure each "
            "timestamp appears only once (e.g. "
            "series[~series.index.duplicated(keep='first')])."
        )

def build_calibration_subset(
    ds: xr.Dataset, 
    dates: Sequence[Mapping[str, Any]],
    allow_date_mismatch: bool = False
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
    allow_date_mismatch : bool, default False
        If ``True``, issue a warning instead of raising an error when the
        requested calibration range extends beyond the dataset's time span.
        Missing time steps are filled with ``NaN`` and HydroErr metrics will
        compute on the overlapping valid data.

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
        if allow_date_mismatch:
            warnings.warn(
                "Requested calibration range extends beyond dataset time span. "
                f"Dataset time range: [{orig_min}, {orig_max}], "
                f"requested range: [{requested_min}, {requested_max}], "
                f"inferred freq: {freq}. "
                "Missing values will be filled with NaN."
            )
        else:
            raise KeyError(
                "Requested calibration range beyond simulation time-series. "
                f"Dataset time range: [{orig_min}, {orig_max}], "
                f"requested range: [{requested_min}, {requested_max}], "
                f"inferred freq: {freq}"
            )

    # Reindex (no fill method => NaNs)
    out = ds.reindex(time=union_index)
    return out

def resample_per_variable(
    ds: xr.Dataset,
    rule: str = "1D",
    dim: str = "time",
    methods: Optional[Dict[str, Union[str, Callable]]] = None,
    default: Optional[Union[str, Callable]] = None,
    origin: Optional[Any] = None,
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
    origin : str or :class:`pandas.Timestamp`, optional
        Timestamp (or xarray origin keyword) anchoring the resampling bins.
        Pass the first timestamp of the observation time index so the
        aggregated labels land exactly on the observation timestamps.
        ``None`` keeps xarray's default (``'start_day'``).
    **kwargs
        Additional keyword arguments passed to the reducer (for example,
        ``skipna=True``, ``keep_attrs=True``).

    Returns
    -------
    :class:`xarray.Dataset`
        A dataset containing the resampled variables.

    Notes
    -----
    Data variables that do not carry the resampling dimension (e.g. the
    scalar ``crs`` variable written by MESH) cannot be resampled and are
    passed through unchanged.

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
        if dim not in ds[var].dims:
            # static (non-time) variable: cannot be resampled, keep as-is
            out[var] = ds[var]
            continue
        reducer = methods.get(var, default)
        if reducer is None:
            continue
        resampler_kwargs = {}
        if origin is not None:
            resampler_kwargs['origin'] = origin
        resampler = ds[var].resample({dim: rule}, **resampler_kwargs)
        if isinstance(reducer, str):
            if not hasattr(resampler, reducer):
                raise ValueError(f"Reducer '{reducer}' not available for '{var}'")
            out[var] = getattr(resampler, reducer)(**kwargs)
        elif callable(reducer):
            out[var] = resampler.reduce(reducer, **kwargs)
        else:
            raise TypeError(f"Reducer for '{var}' must be a string or callable")
    return xr.Dataset(out)

def align_calibration_subset(
    simulations: xr.Dataset,
    observations: xr.Dataset,
    dates: Sequence[Mapping[str, Any]],
    allow_date_mismatch: bool = False,
) -> Tuple[xr.Dataset, xr.Dataset]:
    """Extract the calibration window and align both datasets on one time axis.

    The requested ``dates`` are applied exactly to the simulation's native
    time grid (see :func:`build_calibration_subset`), which requires the
    simulation to carry those timestamps.  The simulation is then aggregated
    to the observations' time resolution, with the aggregation anchored to
    the observations' own time grid (phase) so the aggregated labels
    coincide with the observation timestamps.  Finally, the observations are
    reindexed onto that aggregated grid, which is an exact alignment when
    the observation series is sampled on the same regular grid.

    Both returned datasets therefore share one identical ``time`` axis and
    can enter element-wise objective-function computations directly.

    Parameters
    ----------
    simulations : :class:`xarray.Dataset`
        Simulation results containing ``subbasin`` and ``time`` dimensions.
    observations : :class:`xarray.Dataset`
        Observation dataset with ``subbasin`` and ``time`` dimensions, a
        per-station ``freq`` variable and a regular time index.
    dates : sequence of mapping
        Iterable of ``{"start": <str>, "end": <str>}`` dictionaries defining
        the closed calibration intervals (see :func:`build_calibration_subset`).
    allow_date_mismatch : bool, default False
        If ``True``, allow the requested calibration range to extend beyond
        the simulation's time span (missing simulation steps are filled with
        NaN); otherwise a ``KeyError`` is raised.

    Returns
    -------
    tuple of :class:`xarray.Dataset`
        ``(sim_sub, obs_sub)`` — the simulation aggregated to the
        observation time resolution and the observations aligned onto the
        same time axis.
    """
    # (1) extract the requested period from the simulation at its native
    # time grid; bounds-check against the simulation time span
    sim_sub = build_calibration_subset(
        simulations, dates, allow_date_mismatch=allow_date_mismatch
    )

    # (2) aggregate the simulation to the observations' time resolution if
    # the time-steps differ, anchoring the bins to the observations' own
    # time grid so the aggregated labels land exactly on the observation
    # timestamps
    obs_ts = str(np.unique(observations['freq'].values)[0])
    sim_ts = xr.infer_freq(sim_sub['time'])
    ts_interval = pd.tseries.frequencies.to_offset
    if ts_interval(obs_ts) != ts_interval(sim_ts):
        # only time-dependent variables can be resampled; static
        # variables (e.g. the scalar `crs` written by MESH) are
        # ignored here and kept as-is by `resample_per_variable`
        time_vars = [v for v in sim_sub.data_vars
                     if 'time' in sim_sub[v].dims]
        # build per-variable resampling methods from `defaults.json`;
        # aggregation keys (e.g., "mean", "sum") must match xarray
        # resampler method names
        resample_methods = {
            var: how
            for how, variables in DEFAULTS.get('output_variables', {}).items()
            for var in variables
            if var in time_vars
        }
        # variables not listed in `defaults.json` fall back to `mean`
        unlisted_vars = sorted(set(time_vars) - set(resample_methods))
        if unlisted_vars:
            warnings.warn(
                f"Variable(s) {unlisted_vars} not listed under "
                "'output_variables' in defaults.json — resampling with "
                "'mean'. Add them to defaults.json to control their "
                "aggregation."
            )
        sim_sub = resample_per_variable(
            sim_sub,
            rule=obs_ts,
            methods=resample_methods,
            default='mean',
            origin=observations.indexes['time'][0],
        )

    # (3) align the observations onto the (aggregated) simulation grid.
    # Because the aggregation above is anchored to the observations' own
    # time grid, this reindex is exact and introduces no NaNs; genuinely
    # missing observation days remain NaN and are handled downstream
    # (station filter, HydroErr skipna).
    obs_sub = observations.reindex(time=sim_sub['time'])
    return sim_sub, obs_sub

def build_station_series(sim_sub, obs_sub, flux_var, station_ids):
    """Build per-station simulation and observation series for a given flux.

    Returns dictionaries keyed by station name with ``pd.Series`` values.
    """
    sim_series, obs_series = {}, {}
    for st in station_ids:
        name = obs_sub['name'].sel(subbasin=st).to_numpy().tolist()
        sim_series[name] = sim_sub[flux_var].sel(subbasin=st).to_series()
        obs_series[name] = obs_sub[flux_var].sel(subbasin=st).to_series()
    return sim_series, obs_series

def compute_metric_dict(sim_series, obs_series, metric_name, metric_kwargs=None):
    """Compute a metric for each station.

    ``metric_name`` can be a string (looked up from *HydroErr*) or a
    callable that accepts ``(simulated, observed)`` and returns a scalar.

    ``metric_kwargs``, if provided, is forwarded verbatim to the metric
    function as ``metric_func(simulated, observed, **metric_kwargs)``. This
    lets callers use per-metric options such as HydroErr's
    ``remove_zero``/``remove_neg`` valid-pair masking.

    Returns a dictionary keyed by station name with scalar metric values.

    .. important::

       **NOTE**: When providing a custom callable, the **first** argument
       must be the **simulated** time-series and the **second** argument
       must be the **observed** time-series. For example::

           def my_metric(simulated, observed):
               return some_scalar

    """
    if callable(metric_name):
        metric_func = metric_name
    elif hasattr(HydroErr, metric_name):
        metric_func = getattr(HydroErr, metric_name)
    else:
        raise ValueError(
            f"Metric '{metric_name}' is not a recognized HydroErr "
            f"metric and is not a callable. Provide a valid HydroErr "
            f"metric name or a callable accepting (simulated, observed)."
        )
    metric_kwargs = metric_kwargs or {}
    return {
        name: metric_func(sim_series[name], obs_series[name], **metric_kwargs)
        for name in obs_series
    }

def write_of_csv(output_dir, group, flux_var, metric_name, index, value, prefix=''):
    """Write a single objective function value to a CSV file.
    
    Parameters
    ----------
    output_dir : str
        Directory where the CSV file will be written.
    group : str
        Group name (e.g., 'flux', 'custom', 'helper').
    flux_var : str
        Flux variable name (e.g., 'QO').
    metric_name : str
        Metric name (e.g., 'nse').
    index : int
        Index for multiple expressions of the same metric.
    value : float
        The metric value to write.
    prefix : str, optional
        Prefix to prepend to the filename (e.g., 'constraint_').
    """
    path = os.path.join(
        output_dir,
        f'{prefix}{group}_{flux_var.upper()}_{metric_name}_{index}.csv',
    )
    with open(path, 'w') as f:
        f.write(f'{value}')

def write_penalty_values(eval_config, penalty=1e10):
    """Write penalty values for all configured objective functions and constraints.

    The filenames mirror the ResponseVars rendered by the OSTRICH template:
    every non-helper group contributes one file per expression, with the
    expression index restarting at 1 for each metric. Helper groups only
    produce intermediate values and are therefore skipped.
    """
    output_dir = os.path.join('./etc', 'eval')
    
    # Write penalty values for objective functions
    for group, group_metrics in (eval_config.get('objective_functions') or {}).items():
        if 'helper' in group:
            continue
        for flux_var, metrics in group_metrics.items():
            for metric_name, spec in metrics.items():
                metric_key = metric_name.__name__ if callable(metric_name) else metric_name
                expressions, _ = normalize_metric_spec(spec)
                for idx in range(1, len(expressions) + 1):
                    write_of_csv(output_dir, group, flux_var, metric_key, idx, penalty)
    
    # Write penalty values for constraints
    constraints = eval_config.get('constraints') or {}
    for group, group_metrics in constraints.items():
        if 'helper' in group:
            continue
        for flux_var, metrics in group_metrics.items():
            for metric_name, metric_info in metrics.items():
                metric_key = metric_name.__name__ if callable(metric_name) else metric_name
                expressions = normalize_expressions(metric_info.get('expressions', []))
                for idx in range(1, len(expressions) + 1):
                    write_of_csv(output_dir, group, flux_var, metric_key, idx, penalty, prefix='constraint_')

def normalize_expressions(value):
    """Ensure expressions are a list; wrap a bare string into a single-element list."""
    if isinstance(value, list):
        return value
    return [value]

def normalize_metric_spec(value):
    """Normalize a metric specification into ``(expressions, kwargs)``.

    Objective/constraint metric values historically accepted two forms:

    - a plain expression string, or
    - a list of expression strings.

    This function additionally accepts a mapping of the form::

        {"expressions": <str or list>, "kwargs": {<metric function kwargs>}}

    ``expressions`` is required; ``kwargs`` is optional and defaults to an
    empty mapping. Unknown keys raise :class:`ValueError`.

    Returns
    -------
    tuple
        ``(expressions, kwargs)`` where ``expressions`` is always a list and
        ``kwargs`` is always a dict.
    """
    if isinstance(value, dict):
        unknown = set(value) - {'expressions', 'kwargs'}
        if unknown:
            raise ValueError(
                f"Unknown key(s) {sorted(unknown)} in metric specification; "
                "allowed keys are 'expressions' and 'kwargs'."
            )
        if 'expressions' not in value:
            raise ValueError(
                "Metric specification is missing the required 'expressions' key."
            )
        kwargs = value.get('kwargs', {})
        if kwargs is None:
            kwargs = {}
        if not isinstance(kwargs, dict):
            raise TypeError(
                "Metric specification 'kwargs' must be a mapping of keyword "
                f"arguments, got {type(kwargs).__name__}."
            )
        return normalize_expressions(value['expressions']), kwargs
    return normalize_expressions(value), {}

def validate_metric_specs(eval_config):
    """Validate per-metric specifications before any model execution.

    Ensures metric mappings use only supported keys (via
    :func:`normalize_metric_spec`) and that ``kwargs`` are only attached where
    a metric function exists to consume them: HydroErr metrics, user-defined
    callables, and standard flux-based constraints. Derived helpers and custom
    entries are expressions over other metrics, so ``kwargs`` on them are a
    configuration error.

    Raises
    ------
    ValueError
        If a metric specification is malformed or carries ``kwargs`` where
        they cannot be forwarded to a metric function.
    TypeError
        If ``kwargs`` is not a mapping.
    """
    def _metric_key(metric_name):
        return metric_name.__name__ if callable(metric_name) else metric_name

    def _is_metric_func(metric_name):
        return metric_name in hydro_err_ofs or callable(metric_name)

    def _reject_derived_kwargs(metric_key, group, kind):
        raise ValueError(
            f"{kind} '{metric_key}' in group '{group}' is an expression over "
            "other metrics and cannot carry 'kwargs' — there is no metric "
            "function to forward them to."
        )

    objective_functions = eval_config.get('objective_functions') or {}
    for group, group_metrics in objective_functions.items():
        for flux_var, metrics in group_metrics.items():
            for metric_name, spec in metrics.items():
                metric_kwargs = normalize_metric_spec(spec)[1]
                if 'helper' in group and not _is_metric_func(metric_name):
                    if metric_kwargs:
                        _reject_derived_kwargs(
                            _metric_key(metric_name), group, 'Derived helper'
                        )
                elif 'custom' in group:
                    if metric_kwargs:
                        _reject_derived_kwargs(
                            _metric_key(metric_name), group, 'Custom metric'
                        )

    constraints = eval_config.get('constraints') or {}
    for group, group_metrics in constraints.items():
        for flux_var, metrics in group_metrics.items():
            for metric_name, metric_info in metrics.items():
                metric_kwargs = metric_info.get('kwargs') if isinstance(metric_info, dict) else None
                if metric_kwargs is None:
                    metric_kwargs = {}
                if not isinstance(metric_kwargs, dict):
                    raise TypeError(
                        f"Constraint '{_metric_key(metric_name)}' in group "
                        f"'{group}', flux '{flux_var}' has 'kwargs' of type "
                        f"{type(metric_kwargs).__name__}; expected a mapping."
                    )
                if not metric_kwargs:
                    continue
                if 'helper' in group and not _is_metric_func(metric_name):
                    _reject_derived_kwargs(
                        _metric_key(metric_name), group, 'Derived constraint helper'
                    )
                if 'custom' in group:
                    _reject_derived_kwargs(
                        _metric_key(metric_name), group, 'Custom constraint metric'
                    )

    return

def rewrite_expr(expression, keys, flux_var, dict_name):
    """Replace metric names in ``expression`` with explicit dict references.

    Each key in ``keys`` is replaced with ``{dict_name}['{flux_var}']['{key}']``.
    """
    result = expression
    for k in keys:
        pattern = rf'\b{re.escape(k)}\b'
        replacement = f"{dict_name}['{flux_var}']['{k}']"
        result = re.sub(pattern, replacement, result)
    return result

if __name__ == "__main__":
    # read the `json` configuration file
    with open("./etc/eval/eval.json", "r") as f:
        eval_config = json.load(f, object_hook=_make_object_hook())

    # Reconstruct user-defined callable metrics from stored source code.
    _user_metric_funcs = {}
    for name, source in eval_config.pop('user_defined_metrics', {}).items():
        _ns = {'np': np, 'numpy': np}
        exec(textwrap.dedent(source), _ns)
        _user_metric_funcs[name] = _ns[name]

    # Replace string keys with the reconstructed callables in objective_functions.
    if _user_metric_funcs:
        for group in eval_config['objective_functions']:
            for flux_var in eval_config['objective_functions'][group]:
                metrics = eval_config['objective_functions'][group][flux_var]
                for name, func in _user_metric_funcs.items():
                    if name in metrics:
                        metrics[func] = metrics.pop(name)

    # Replace string keys with the reconstructed callables in constraints.
    if _user_metric_funcs:
        constraints = eval_config.get('constraints')
        if constraints:
            for group in constraints:
                for flux_var in constraints[group]:
                    metrics = constraints[group][flux_var]
                    for name, func in _user_metric_funcs.items():
                        if name in metrics:
                            metrics[func] = metrics.pop(name)

    # Validate per-metric specifications early: a malformed spec or `kwargs`
    # attached to an expression-only metric must fail before any model run
    # rather than being masked as an OSTRICH penalty value.
    validate_metric_specs(eval_config)

    # empty the output directory before anything else
    _reset_dir(
        os.path.join(
            eval_config['model_instance_path'],
            eval_config['results_path']
        )
    )

    # read the observation file
    observations = xr.open_dataset(eval_config['observations_file'])

    # sanity check the observation data before running the model
    try:
        validate_dataset_dims(observations, 'observation data')
    except ValueError as e:
        warnings.warn(
            f'OBSERVATION DATA CORRUPTED: {str(e)}. '
            'OBJECTIVE FUNCTION VALUES WILL BE SET TO A LARGE NUMBER.'
        )
        write_penalty_values(eval_config)
        sys.exit(0)

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
    my_env = os.environ.copy()
    try:
        with open(os.path.join('model_run.log'), 'w') as f:
            subprocess.run(
                ['./' + eval_config['model_executable']],
                cwd=eval_config['model_instance_path'],
                check=True,
                env=my_env,
                stdout=f,
                stderr=f)
    except subprocess.CalledProcessError as e:
        warnings.warn(
            f'MODEL EXECUTION FAILED WITH ERROR CODE {e.returncode}. '
            'OBJECTIVE FUNCTION VALUES WILL BE SET TO A LARGE NUMBER.'
        )
        write_penalty_values(eval_config)
        sys.exit(0)

    # post-process simulation results and evaluate objective functions
    try:
        simulations = xr.open_dataset(
            os.path.join(
                eval_config['model_instance_path'],
                eval_config['results_path'],
                eval_config['output_files'][0][0]
            )
        )

        # sanity check: both datasets need 'subbasin' and 'time' dims,
        # and 'time' must be an index coordinate
        validate_dataset_dims(simulations, 'simulation results')
        validate_dataset_dims(observations, 'observation data')

        # subset to calibration dates: extract the requested period from
        # the simulation at its native time grid, aggregate it to the
        # observations' time resolution, and align the observations onto
        # the same time axis
        allow_mismatch = eval_config.get('allow_date_mismatch', False)
        sim_sub, obs_sub = align_calibration_subset(
            simulations,
            observations,
            eval_config.get('dates'),
            allow_date_mismatch=allow_mismatch,
        )

        # Filter out stations whose observations are entirely NaN for
        # the selected calibration period.  Keeps only stations that have
        # at least one non-NaN value in any observed data variable.
        all_station_ids = obs_sub.subbasin.to_numpy().tolist()
        obs_data_vars = [
            v for v in obs_sub.data_vars
            if v not in ('applied_scale_factor', 'applied_offset_factor')
        ]
        station_ids = []
        for st in all_station_ids:
            st_slice = obs_sub.sel(subbasin=st)
            all_nan = all(
                np.isnan(st_slice[v].values).all() for v in obs_data_vars
            )
            if all_nan:
                st_name = obs_sub['name'].sel(subbasin=st).to_numpy().tolist()
                warnings.warn(
                    f"Station '{st_name}' (subbasin={st}) has entirely NaN "
                    f"observations for the calibration period — excluding "
                    f"from evaluation."
                )
            else:
                station_ids.append(st)

        if len(station_ids) == 0:
            raise ValueError(
                "All stations have entirely NaN observations for the "
                "calibration period. Cannot compute objective functions."
            )

        station_names = obs_sub.name.sel(subbasin=station_ids).to_numpy().tolist()

        # evaluate objective functions
        of_values = {}
        helper_ofs = {}
        custom_ofs = {}
        output_dir = os.path.join('./etc', 'eval')

        for group, group_metrics in eval_config.get('objective_functions').items():

            # helpers: intermediate metrics not written to CSV
            if 'helper' in group:
                for flux_var, metrics in group_metrics.items():
                    helper_ofs[flux_var] = {}

                    for metric_name in metrics:
                        metric_key = metric_name.__name__ if callable(metric_name) else metric_name
                        helper_ofs[flux_var][metric_key] = []
                        expressions, metric_kwargs = normalize_metric_spec(
                            metrics[metric_name]
                        )

                        if metric_name in hydro_err_ofs or callable(metric_name):
                            # standard HydroErr metric or user-defined callable
                            sim_series, obs_series = build_station_series(
                                sim_sub, obs_sub, flux_var, station_ids
                            )
                            station_metrics = compute_metric_dict(
                                sim_series, obs_series, metric_name, metric_kwargs
                            )
                            # Warn about stations producing non-finite metrics
                            nan_stations = [
                                n for n, v in station_metrics.items()
                                if not np.isfinite(v)
                            ]
                            if nan_stations:
                                warnings.warn(
                                    f"Metric '{metric_key}' produced non-finite "
                                    f"value for station(s) {nan_stations} on flux "
                                    f"'{flux_var}' — excluding from aggregate "
                                    f"evaluation."
                                )
                            for expr in expressions:
                                try:
                                    metric_value = ne.evaluate(expr, local_dict=station_metrics)
                                except KeyError:
                                    # Station was excluded (e.g., all-NaN observations)
                                    continue
                                if np.isfinite(metric_value):
                                    helper_ofs[flux_var][metric_key].append(metric_value)
                        else:
                            # derived helper: expression referencing previously computed helpers
                            if metric_kwargs:
                                raise ValueError(
                                    f"Derived helper '{metric_key}' in group "
                                    f"'{group}' cannot carry 'kwargs'."
                                )
                            for k in helper_ofs[flux_var]:
                                if isinstance(helper_ofs[flux_var][k], list):
                                    helper_ofs[flux_var][k] = np.array(helper_ofs[flux_var][k])

                            existing_keys = [k for k in helper_ofs[flux_var] if k != metric_key]
                            for expr in expressions:
                                rewritten = rewrite_expr(expr, existing_keys, flux_var, 'helper_ofs')
                                metric_value = eval(rewritten)
                                helper_ofs[flux_var][metric_key] = metric_value

            # custom: expressions referencing helpers, written to CSV
            elif 'custom' in group:
                for flux_var, metrics in group_metrics.items():
                    custom_ofs[flux_var] = {}

                    for metric_name in metrics:
                        metric_key = metric_name.__name__ if callable(metric_name) else metric_name
                        custom_ofs[flux_var][metric_key] = []

                        helper_keys = list(helper_ofs.get(flux_var, {}).keys())
                        custom_keys = [k for k in custom_ofs[flux_var] if k != metric_key]
                        expressions, metric_kwargs = normalize_metric_spec(
                            metrics[metric_name]
                        )
                        if metric_kwargs:
                            raise ValueError(
                                f"Custom objective function '{metric_key}' in "
                                f"group '{group}' cannot carry 'kwargs' — it is "
                                "an expression over other metrics."
                            )

                        for idx, expr in enumerate(expressions, start=1):
                            rewritten = rewrite_expr(expr, helper_keys, flux_var, 'helper_ofs')
                            rewritten = rewrite_expr(rewritten, custom_keys, flux_var, 'custom_ofs')
                            metric_value = eval(rewritten)
                            custom_ofs[flux_var][metric_key] = metric_value
                            write_of_csv(output_dir, group, flux_var, metric_key, idx, metric_value)

            # standard flux-based objective functions using HydroErr
            else:
                for flux_var, metrics in group_metrics.items():
                    of_values[flux_var] = {}
                    sim_series, obs_series = build_station_series(
                        sim_sub, obs_sub, flux_var, station_ids
                    )

                    for metric_name, spec in metrics.items():
                        metric_key = metric_name.__name__ if callable(metric_name) else metric_name
                        of_values[flux_var][metric_key] = []
                        expressions, metric_kwargs = normalize_metric_spec(spec)
                        station_metrics = compute_metric_dict(
                            sim_series, obs_series, metric_name, metric_kwargs
                        )
                        nan_stations = [
                            n for n, v in station_metrics.items()
                            if not np.isfinite(v)
                        ]
                        if nan_stations:
                            warnings.warn(
                                f"Metric '{metric_key}' produced non-finite "
                                f"value for station(s) {nan_stations} on flux "
                                f"'{flux_var}'."
                            )

                        for idx, expr in enumerate(expressions, start=1):
                            try:
                                metric_value = ne.evaluate(expr, local_dict=station_metrics)
                            except KeyError:
                                continue
                            of_values[flux_var][metric_key] = metric_value
                            write_of_csv(output_dir, group, flux_var, metric_key, idx, metric_value)

        # Evaluate constraints (if present)
        constraints = eval_config.get('constraints', {})
        if constraints:
            constraint_helper_ofs = {}
            constraint_custom_ofs = {}

            for group, group_metrics in constraints.items():
                # helpers: intermediate constraint metrics not written to CSV
                if 'helper' in group:
                    for flux_var, metrics in group_metrics.items():
                        constraint_helper_ofs[flux_var] = {}

                        for metric_name in metrics:
                            metric_key = metric_name.__name__ if callable(metric_name) else metric_name
                            metric_info = metrics[metric_name]
                            constraint_helper_ofs[flux_var][metric_key] = []
                            metric_kwargs = metric_info.get('kwargs') or {}
                            expressions = normalize_expressions(metric_info.get('expressions'))

                            if metric_name in hydro_err_ofs or callable(metric_name):
                                # standard HydroErr metric or user-defined callable
                                sim_series, obs_series = build_station_series(
                                    sim_sub, obs_sub, flux_var, station_ids
                                )
                                station_metrics = compute_metric_dict(
                                    sim_series, obs_series, metric_name, metric_kwargs
                                )
                                nan_stations = [
                                    n for n, v in station_metrics.items()
                                    if not np.isfinite(v)
                                ]
                                if nan_stations:
                                    warnings.warn(
                                        f"Constraint metric '{metric_key}' produced non-finite "
                                        f"value for station(s) {nan_stations} on flux "
                                        f"'{flux_var}' — excluding from aggregate evaluation."
                                    )
                                for expr in expressions:
                                    try:
                                        metric_value = ne.evaluate(expr, local_dict=station_metrics)
                                    except KeyError:
                                        continue
                                    if np.isfinite(metric_value):
                                        constraint_helper_ofs[flux_var][metric_key].append(metric_value)
                            else:
                                # derived helper: expression referencing previously computed constraint helpers
                                if metric_kwargs:
                                    raise ValueError(
                                        f"Derived constraint helper '{metric_key}' in "
                                        f"group '{group}' cannot carry 'kwargs'."
                                    )
                                for k in constraint_helper_ofs[flux_var]:
                                    if isinstance(constraint_helper_ofs[flux_var][k], list):
                                        constraint_helper_ofs[flux_var][k] = np.array(constraint_helper_ofs[flux_var][k])

                                existing_keys = [k for k in constraint_helper_ofs[flux_var] if k != metric_key]
                                for expr in expressions:
                                    rewritten = rewrite_expr(expr, existing_keys, flux_var, 'constraint_helper_ofs')
                                    metric_value = eval(rewritten)
                                    constraint_helper_ofs[flux_var][metric_key] = metric_value

                # custom: expressions referencing constraint helpers, written to CSV
                elif 'custom' in group:
                    for flux_var, metrics in group_metrics.items():
                        constraint_custom_ofs[flux_var] = {}

                        for metric_name in metrics:
                            metric_key = metric_name.__name__ if callable(metric_name) else metric_name
                            metric_info = metrics[metric_name]
                            constraint_custom_ofs[flux_var][metric_key] = []

                            helper_keys = list(constraint_helper_ofs.get(flux_var, {}).keys())
                            custom_keys = [k for k in constraint_custom_ofs[flux_var] if k != metric_key]
                            metric_kwargs = metric_info.get('kwargs') or {}
                            if metric_kwargs:
                                raise ValueError(
                                    f"Custom constraint metric '{metric_key}' in "
                                    f"group '{group}' cannot carry 'kwargs' — it is "
                                    "an expression over other metrics."
                                )
                            expressions = normalize_expressions(metric_info.get('expressions'))

                            for idx, expr in enumerate(expressions, start=1):
                                rewritten = rewrite_expr(expr, helper_keys, flux_var, 'constraint_helper_ofs')
                                rewritten = rewrite_expr(rewritten, custom_keys, flux_var, 'constraint_custom_ofs')
                                metric_value = eval(rewritten)
                                constraint_custom_ofs[flux_var][metric_key] = metric_value
                                write_of_csv(output_dir, group, flux_var, metric_key, idx, metric_value, prefix='constraint_')

                # standard flux-based constraint metrics using HydroErr
                else:
                    for flux_var, metrics in group_metrics.items():
                        sim_series, obs_series = build_station_series(
                            sim_sub, obs_sub, flux_var, station_ids
                        )

                        for metric_name, metric_info in metrics.items():
                            metric_key = metric_name.__name__ if callable(metric_name) else metric_name
                            metric_kwargs = metric_info.get('kwargs') or {}
                            station_metrics = compute_metric_dict(
                                sim_series, obs_series, metric_name, metric_kwargs
                            )
                            nan_stations = [
                                n for n, v in station_metrics.items()
                                if not np.isfinite(v)
                            ]
                            if nan_stations:
                                warnings.warn(
                                    f"Constraint metric '{metric_key}' produced non-finite "
                                    f"value for station(s) {nan_stations} on flux "
                                    f"'{flux_var}'."
                                )

                            expressions = normalize_expressions(metric_info.get('expressions'))
                            for idx, expr in enumerate(expressions, start=1):
                                try:
                                    metric_value = ne.evaluate(expr, local_dict=station_metrics)
                                except KeyError:
                                    continue
                                write_of_csv(output_dir, group, flux_var, metric_key, idx, metric_value, prefix='constraint_')

    except (ValueError, TypeError, KeyError) as e:
        warnings.warn(
            f'MODEL OUTPUT CORRUPTED: {str(e)}. '
            'OBJECTIVE FUNCTION VALUES WILL BE SET TO A LARGE NUMBER.'
        )
        write_penalty_values(eval_config)