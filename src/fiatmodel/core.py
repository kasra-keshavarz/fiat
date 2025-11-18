"""FIATModel main entry point"""

# 3rd-party imports
import pandas as pd
import xarray as xr
import numpy as np

import pint
import pint_xarray  # noqa: F401  # registers the .pint accessor

# build-in imports
import json
import sys
import os
import re
import shutil

from importlib.resources import (
    files,
    as_file
)
from typing import (
    Dict,
    List,
    Union,
)
from pathlib import Path

# internal imports
from .utils import *

# defining custom types
if sys.version_info >= (3, 10):
    from typing import TypeAlias
    PathLike: TypeAlias = Union[str, Path]
else:
    PathLike = Union[str, Path]

# defining global constants
# Pint registry
ureg = pint.UnitRegistry()
# This line fixes: ValueError: invalid registry. Please enable 'force_ndarray_like' or 'force_ndarray'.
ureg.force_ndarray_like = True  # or: ureg.force_ndarray = True (stricter)
pint.set_application_registry(ureg)
# global re patterns for numeric string parsing:
#   Precompile regexes for speed/readability
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
# current enviornment
MYENV = os.environ.copy()


class Calibration(object):
    """Calibration workflow orchestrator for FIAT.

    Coordinates the calibration engine and the hydrologic model, manages
    observations, prepares run directories, and generates evaluation assets.

    Notes
    -----
    - The class builds model- and calibration-specific helper objects based on
      the provided `model_software` and `calibration_software` names.
    - Supported values are currently `"mesh"` for the model and `"ostrich"`
      for the calibration engine.
    - Users are encouraged to add new model and calibration software recipes
      by extending available workflows.

    Attributes
    ----------
    calibration_software : str
        Selected calibration engine name (e.g., ``"ostrich"``).
    model_software : str
        Selected model name (e.g., ``"mesh"``).
    calibration_config : dict or None
        Configuration dictionary used by the calibration engine.
    model_config : dict or None
        Configuration dictionary used by the model.
    model : object
        Model adapter instance constructed from ``model_software`` (e.g.,
        ``fiatmodel.models.mesh.MESH``).
    calibration : object
        Calibration engine instance constructed from ``calibration_software``
        (e.g., ``fiatmodel.calibration.OstrichTemplateEngine``).
    observations : xarray.Dataset
        Property that builds and returns the observations dataset on access.
    _obs : list of dict or pandas.Series or pathlib.Path or str
        Raw observation definitions, a time series, or a path to a NetCDF
        file provided by the user. Used internally by the ``observations``
        property. (Private)

    Methods
    -------
    from_json(json_path)
        Construct an instance from a JSON configuration file.
    from_dict(data)
        Construct an instance from a dictionary of constructor arguments.
    to_dict()
        Return a shallow dictionary of instance attributes.
    prepare(output_path=None)
        Render templates, persist observations, and stage evaluation assets.
    observations
        Property returning the observations dataset; includes a setter to
        update the raw observation inputs.
    __repr__()
        Debug representation string.
    __str__()
        Human-readable representation string.
    _eval()
        Internal: create per-iteration evaluation assets for the engine.
    _summarize_fiat_inputs(output_path=None)
        Internal: write a summary JSON capturing the full instance inputs.
    """

    def __init__(
        self,
        calibration_software: str,
        model_software: str,
        calibration_config: Dict = None,
        model_config: Dict = None,
        observations: List[Dict] = None,
    ) -> None:
        """Create a new `Calibration` controller.

        Parameters
        ----------
        calibration_software : str
            Name of the calibration engine (e.g., ``"ostrich"``).
        model_software : str
            Name of the hydrologic model (e.g., ``"mesh"``).
        calibration_config : dict, optional
            Configuration for the calibration engine, including objective
            functions and calibration time window.
        model_config : dict, optional
            Configuration for the model (e.g., executable path, I/O files).
        observations : list of dict, optional
            Observation definitions used for evaluation. See
            the ``observations`` property for the expected schema.

        Notes
        -----
        The constructor immediately builds the model- and calibration-specific
        helper objects based on the chosen software names.
        """
        # check data types
        if not isinstance(calibration_software, str):
            raise TypeError('`calibration_software` must be a string')
        if not isinstance(model_software, str):
            raise TypeError('`model_software` must be a string')
        if calibration_config is not None and not isinstance(calibration_config, dict):
            raise TypeError('`calibration_config` must be a dictionary')
        if model_config is not None and not isinstance(model_config, dict):
            raise TypeError('`model_config` must be a dictionary')
        if observations is not None and not isinstance(observations, list):
            raise TypeError('`observations` must be a list of dictionaries')

        # assign object attributes
        self.calibration_software = calibration_software.lower()
        self.model_software = model_software.lower()
        self.calibration_config = calibration_config
        self.model_config = model_config
        self._obs = observations

        # build the model-specific object
        match self.model_software:
            case 'mesh':
                from .models.mesh import MESH
                self.model = MESH(
                    config=self.model_config,
                    calibration_software=self.calibration_software,
                    fluxes=self.calibration_config.get('objective_functions').keys(),
                    dates=self.calibration_config.get('dates'),
                    spinup=self.calibration_config.get('spinup_start'),
                )
            case _:
                raise ValueError(f"Unsupported model software: {self.model_software}")

        # build the calibration-specific object
        match self.calibration_software:
            case 'ostrich':
                from .calibration import OstrichTemplateEngine
                self.calibration = OstrichTemplateEngine(
                    config=self.calibration_config,
                    model=self.model,
                )
            case _:
                raise ValueError(f"Unsupported calibration software: {self.calibration_software}")

        return

    @classmethod
    def from_json(cls, json_path: str):
        """Instantiate from a JSON file.

        Parameters
        ----------
        json_path : str
            Path to a JSON file containing keyword arguments compatible with
            the `Calibration` constructor.

        Returns
        -------
        Calibration
            A new instance populated from the JSON configuration.
        """
        with open(json_path, 'r') as f:
            data = json.load(f)
        return cls(**data)

    @classmethod
    def from_dict(cls, data: dict):
        """Instantiate from a dictionary of constructor arguments.

        Parameters
        ----------
        data : dict
            Dictionary of keyword arguments compatible with the `Calibration`
            constructor.

        Returns
        -------
        Calibration
            A new instance populated from the provided dictionary.
        """
        return cls(**data)

    def __repr__(self):
        """Representation string for debugging.

        Returns
        -------
        str
            A concise constructor-like representation.
        """
        return 'Calibration()'

    def __str__(self):
        """Human-readable string representation.

        Returns
        -------
        str
            A concise description of the object.
        """
        return 'Calibration()'

    @property
    def observations(
        self,
    ) -> xr.Dataset:
        """
        Load and process observational data into a quantified xarray.Dataset.

        This method consumes observational data provided either as:
        1) a path to a NetCDF file (.nc or .nc4), or
        2) a list of entry sequences describing time series per computational unit.

        When provided a NetCDF file path, the dataset is opened and quantified using the
        module's Pint unit registry. If a "freq" variable must be present to assure
        the frequency information is interpretted properly. Due to natue of observational
        data, missing timestamps are common, therefore, no inference of frequency
        is performed and the user must provide it explicitly.

        When provided a list of entries, time series are aligned onto the union of all
        timestamps, values are converted to a consistent unit per variable "type" using
        Pint, and the result is assembled into a Dataset with one variable per "type"
        (e.g., "QO") and two primary coordinates: time and the model's computational
        unit kind (e.g., "subbasin").

        Expected entry schema (list of dict)
        ------------------------------------
        - name: str, optional
            Human-readable station identifier.
        - type: str, required
            Observational variable key (e.g., "QO").
        - timeseries: Sequence[tuple[date_like, float]] | pandas.Series, required
            Time series as (date, value) pairs or a pandas Series with a DatetimeIndex.
        - unit: str, required
            Physical unit string compatible across entries of the same "type"
            (e.g., "m3/s"). All entries of the same "type" are converted to a
            common unit (the first encountered unit for that type).
        - computational_unit: str, required
            The model's computational unit kind (e.g., "subbasin"); must match a
            NetCDF dimension name. At least one entry must provide this.
        - computational_unit_id: int, required
            Identifier of the computational unit. Multiple entries may share the
            same identifier for different "type" values.
        - freq: str, optional
            Sampling frequency as a pandas offset alias (e.g., "1D", "1H").
            See: https://pandas.pydata.org/pandas-docs/stable/user_guide/timeseries.html#dateoffset-objects
        - Units: Values for a given "type" are converted to a unified unit using Pint.
          Incompatible units will raise a Pint dimensionality error.

        Examples
        --------
        From a list of entries::

            obs = [
                    "name": "station_1",
                    "type": "QO",
                    "timeseries": [("2020-01-01", 10.5), ("2020-01-02", 12.3)],
                    "unit": "m3/s",
                    "computational_unit": "subbasin",
                    "computational_unit_id": 14,
                    "freq": "1D",
                },
                    "name": "station_2",
                    "type": "QO",
                    "timeseries": pd.Series(
                        data=[2.1, 2.2],
                        index=pd.to_datetime(["2020-01-01", "2020-01-03"])
                    ),
                    "unit": "m3/s",
                    "computational_unit": "subbasin",
                    "computational_unit_id": 42,
                    "freq": "1D",
                },
            model.observation_config = obs
            dsq = model.observations()

        Notes
        -----
        - ``type`` refers to the observational equivalence of the model output.
           As an example, `QO` is a varibale name for routed streamflow output
           from the MESH model, and in case it is used here, it indicates that the
           observational data is streamflow data from the MESH model output.
        - ``timeseries`` is an array-like object containing date-value pairs
           for the observational data. It can also be provided as a pandas Series
           object with datetime index and float values.
        - ``freq`` specifies the frequency of the observational data. The 
           value follows the pandas frequency strings (e.g., '1D' for
           daily, '1h' for hourly). For more details, refer to:
           https://pandas.pydata.org/pandas-docs/stable/user_guide/timeseries.html#dateoffset-objects

        Returns
        -------
        xarray.Dataset
            Observations as an xarray dataset with time and computational
            unit dimensions; variables carry units via the Pint accessor.

        """
        # by default, enable converting units
        convert_units: bool = True

        # if the `observation` is a netcdf file path, read it directly
        if isinstance(self._obs, PathLike):
            # make sure it is a `Path` object
            self._obs = Path(self._obs)
            # extract the suffix
            if self._obs.suffix in ['.nc', '.nc4']:
                dsq = xr.open_dataset(self._obs).pint.quantify(unit_registry=ureg)
                # extract the frequency information from the time coordinate
                # if not provided already as variable
                if "freq" not in dsq.variables:
                    time_index = pd.DatetimeIndex(dsq["time"].values)
                    inferred_freq = pd.infer_freq(time_index)
                    if inferred_freq is not None:
                        dsq["freq"] = inferred_freq
            else:
                raise ValueError(
                    f"Unsupported observation file format: {self._obs.suffix}"
                )
            return dsq

        entries = list(self._obs)

        # Collect per-entry parsed series and metadata
        per_entry_series: List[pd.Series] = []
        per_entry_time_index: List[pd.DatetimeIndex] = []
        per_entry_meta: List[Dict] = []

        for e in entries:
            ts = e.get("timeseries", [])
            if len(ts) == 0:
                idx = pd.DatetimeIndex([])
                vals = np.array([], dtype=float)
            elif isinstance(ts, pd.Series):
                idx = pd.to_datetime(ts.index)
                vals = ts.to_numpy(dtype=float)
            else:
                t, v = zip(*ts)
                idx = pd.to_datetime(list(t))
                vals = np.array(list(v), dtype=float)

            per_entry_series.append(pd.Series(vals, index=idx))
            per_entry_time_index.append(idx)

            per_entry_meta.append(
                dict(
                    name=e.get("name"),
                    typ=e.get("type"),
                    unit=e.get("unit"),
                    cu_kind=e.get("computational_unit"),
                    cu_id=e.get("computational_unit_id"),
                    freq=e.get("freq"),
                )
            )

        # Global coordinates
        global_time = union_sorted_times(per_entry_time_index)

        # Unique computational_unit ids in first-seen order
        seen_ids: List[int] = []
        id_to_first_index: Dict[int, int] = {}
        for m in per_entry_meta:
            cu_id = m["cu_id"]
            if cu_id not in id_to_first_index:
                id_to_first_index[cu_id] = len(seen_ids)
                seen_ids.append(cu_id)
        cu_ids = np.array(seen_ids)
        n_cu = len(cu_ids)
        n_time = len(global_time)

        # Coordinate arrays per computational unit
        names_by_id: Dict[int, str] = {}
        freq_by_id: Dict[int, str] = {}
        cu_kind: str | None = None

        # Prepare containers per type
        arrays_by_type: Dict[str, np.ndarray] = {}
        unit_by_type: Dict[str, str] = {}

        def _ensure_matrix_for_type(typ: str):
            if typ not in arrays_by_type:
                arrays_by_type[typ] = np.full((n_cu, n_time), np.nan, dtype=float)

        # First encountered cu_kind (e.g., 'subbasin')
        for m in per_entry_meta:
            if m["cu_kind"] is not None:
                cu_kind = cu_kind or m["cu_kind"]
        # assign `dim_name` to cu_kind
        dim_name = cu_kind

        # Fill matrices
        for s, m in zip(per_entry_series, per_entry_meta):
            typ = m["typ"]
            unit = m["unit"]
            cu_id = m["cu_id"]
            name = m["name"]
            freq = m["freq"]

            if cu_id not in names_by_id and name is not None:
                names_by_id[cu_id] = name
            if cu_id not in freq_by_id and freq is not None:
                freq_by_id[cu_id] = freq

            _ensure_matrix_for_type(typ)

            # Set reference unit for this type
            if typ not in unit_by_type:
                unit_by_type[typ] = unit
            else:
                if unit != unit_by_type[typ]:
                    if not convert_units:
                        raise ValueError(
                            f"Found inconsistent units for type '{typ}': "
                            f"{unit} vs {unit_by_type[typ]}"
                        )

            # Align times
            s_aligned = s.reindex(global_time)

            # Convert units if needed
            if unit != unit_by_type[typ]:
                q = s_aligned.to_numpy() * ureg(unit)
                s_aligned_vals = q.to(unit_by_type[typ]).magnitude
            else:
                s_aligned_vals = s_aligned.to_numpy()

            row = id_to_first_index[cu_id]
            arrays_by_type[typ][row, :] = s_aligned_vals

        # Build coords
        name_arr = np.array([names_by_id.get(cu, None) for cu in cu_ids], dtype=str)
        freq_arr = np.array([freq_by_id.get(cu, None) for cu in cu_ids], dtype=str)

        coords = {
            dim_name: cu_ids,
            "time": global_time,
            "name": (dim_name, name_arr),
            "freq": (dim_name, freq_arr),
        }

        # Build data variables with units in attrs
        data_vars = {}
        for typ, arr in arrays_by_type.items():
            data_vars[typ] = ((dim_name, "time"), arr, {"units": unit_by_type[typ]})

        ds = xr.Dataset(data_vars=data_vars, coords=coords)

        if cu_kind is not None:
            ds.attrs["computational_unit_kind"] = cu_kind

        # return ds

        # Quantify using the SAME registry we configured above
        quantify_map = {typ: unit for typ, unit in unit_by_type.items()}
        dsq = ds.pint.quantify(quantify_map, unit_registry=ureg)

        return dsq
    @observations.setter
    def observations(self, value: List[Dict] | pd.Series) -> None:
        """Set observational inputs.

        Parameters
        ----------
        value : list of dict or pandas.Series
            Observation definitions or a pre-built series that will be parsed
            into an internal xarray dataset by the getter.
        """
        if not isinstance(value, list | pd.Series):
            raise TypeError("`observations` must be a list of dictionaries or a pandas Series.")
        self._obs = value
        return

    def to_dict(self) -> dict:
        """Convert the object to a dictionary.

        Returns
        -------
        dict
            A shallow dictionary of instance attributes suitable for
            serialization.
        """
        return self.__dict__

    def prepare(
        self,
        output_path: PathLike = None) -> None:
        """Render templates, write observations, and stage evaluation scripts.

        Parameters
        ----------
        output_path : PathLike, optional
            Destination directory for the instance. If omitted, the
            ``instance_path`` from ``calibration_config`` is used.
        """
        # by default, set the output path to `self.calibration_config.instance_path`
        # if not provided, check the input to this function
        if output_path is None:
            output_path = self.calibration_config.get('instance_path')
        else:
            output_path = output_path

        # 1. model part
        self.model.analyze()
        self.model.prepare()

        # 2. calibration part
        self.calibration.generate_optimizer_templates(output_path=output_path)
        self.calibration.generate_parameter_templates(output_path=output_path)
        self.calibration.generate_etc_templates(output_path=output_path)
        self.calibration.generate_model_templates(output_path=output_path)
        self.calibration.generate_obs_templates(output_path=output_path)

        # 3. observation part
        self.observations.to_netcdf(
            os.path.join(
                output_path,
                'observations',
                'observations.nc'
            )
        )

        # 4. evaluation part
        self._eval()

        # 5. summarize FIAT inputs
        self._summarize_fiat_inputs(output_path=output_path)

        return

    def _eval(
        self,
    ) -> None:
        """Create evaluation assets used by the calibration engine.

        Notes
        -----
        Writes a compact ``eval.json`` alongside a templated ``eval.py`` and
        ``defaults.json`` for use during each model evaluation iteration.
        """
        # import necessary model-specific workflow package for
        # evaluation needs
        match self.model_software:
            case 'mesh':
                import meshflow as mf
            case _:
                raise ValueError(f"Unsupported model software: {self.model_software}")

        # Making a dictionary of only necessary information during the evaluation
        # process for both calibration and model objects
        eval_dict = {
            'fiat_instance_path': self.calibration_config.get('instance_path'),
            # because the `eval.py` script will eventually be saved under
            # `<fiat_cache_path>/cpu_<n>/etc/evaluation/`, the model instance
            # path is set to:
            #     <fiat_cache_path>/cpu_<n>/model/
            #         or with the relative path:
            #     ../../model/
            # This path is agnostic to the calibration software being used. These
            # files all must copy for each instance of model evaluation.
            'model_instance_path': './model/',
            'model_executable': self.model_config.get('executable'),
            'dates': self.calibration_config.get('dates'),
            'objective_functions': self.calibration_config.get('objective_functions'),
            'results_path': 'results',
            'output_files': [self.model.outputs],
            'observations_file': os.path.join(
                self.calibration_config.get('instance_path'),
                'observations',
                'observations.nc'
            ),
            # based on the calibration_software.templating.py engines, the 
            # `self.model.parameters.keys()` and `self.model.others.keys()`
            # are templated under:
            #     <fiat_instance_path>/etc/templates/<key>.json
            #          Or with the relative path:
            #     ../templates/<key>.json
            # `parameters` need to be recreated in each iteration
            #  USING `EVAL` PATH
            'parameters': {
                key: os.path.join('../eval', f'{key}.json')
                         for key in self.model.parameters.keys()},
            # `others` are static in each iteration but necessary to
            # be read by the script---USING `TEMPLATES` PATH
            'others': {
                key: os.path.join('../templates', f'{key}.json')
                         for key in self.model.others.keys()}
        }

        # dumping the dictionary into a JSON file for the evaluation script
        eval_path = os.path.join(
            self.calibration_config.get('instance_path'),
            'etc',
            'eval',
            'eval.json'
        )
        with open(eval_path, 'w') as f:
            json.dump(eval_dict, f, indent=4)

        # now also move the eval.py file
        rq = files('fiatmodel.models.mesh').joinpath('eval.py')

        with as_file(rq) as src_path:
            shutil.copy2(src_path, os.path.join(eval_dict['fiat_instance_path'], 'etc', 'eval', 'eval.py'))

        # also copy the defaults.json file
        rq_defaults = files('fiatmodel.models.mesh').joinpath('defaults.json')

        with as_file(rq_defaults) as src_defaults_path:
            shutil.copy2(src_defaults_path, os.path.join(eval_dict['fiat_instance_path'], 'etc', 'eval', 'defaults.json'))

        return

    def _summarize_fiat_inputs(
        self,
        output_path: PathLike = None,
    ) -> None:
        """Write a summary file capturing the full FIAT instance inputs.

        Parameters
        ----------
        output_path : PathLike, optional
            Directory where ``fiat_instance.json`` is written. Defaults to
            ``calibration_config['instance_path']`` when not provided.
        """
        summary_dict = {
            'calibration_software': self.calibration_software,
            'model_software': self.model_software,
            'calibration_config': self.calibration_config,
            'model_config': self.model_config,
            'observations': self._obs,
        }

        # check whether the `timeseries` keys in `observations` need to be
        # summarized or not
        for obs in summary_dict['observations']:
            if 'timeseries' in obs:
                ts = obs['timeseries']
                if isinstance(ts, pd.Series):
                    # convert to list of tuples
                    obs['timeseries'] = list(zip(
                        ts.index.astype(str).to_list(),
                        ts.to_numpy(dtype=float).tolist()
                    ))
                else:
                    # make sure all values are converted to float
                    obs['timeseries'] = list(
                        (str(t), float(v)) for t, v in ts
                    )

        if output_path is None:
            output_path = self.calibration_config.get('instance_path')

        summary_path = os.path.join(
            output_path,
            'fiat_instance.json',
        )
        with open(summary_path, 'w') as f:
            json.dump(summary_dict, f, indent=4)

        return
