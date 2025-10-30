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
import inspect
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
    """Main Calibration class of FIAT package.
    """

    def __init__(
        self,
        calibration_software: str,
        model_software: str,
        calibration_config: Dict = None,
        model_config: Dict = None,
        observations: List[Dict] = None,
    ) -> None:
        """
        Initialize the Calibration class.

        Parameters
        ----------
        calibration_software : str
            The software used for calibration. Default is 'ostrich'.
        model_software : str
            The software used for the model. Default is 'mesh'.
        observations : List[Dict]
            Calibration timeseries to be used in model evaluation
            iterations. Each dictionary in the list should contain
            the following keys:
            - 'type': The type of calibration data (e.g., 'QO' for
              observed streamflow).
            - 'location': A dictionary with 'latitude' and 'longitude'
              keys specifying the location of the observation.
            - 'timeseries': A list of tuples containing date-value pairs
              for the calibration data.
            - 'units': The units of the calibration data.
            - 'freq': The frequency of the calibration data (e.g., '1D'
              for daily data).
        model_config : Dict
            Configuration parameters for the model software.

        Returns
        -------
        None

        Notes
        -----
        - The `observations` parameter must follow a specific structure
          to properly load the data. An example is given in the docstring
          of the `observations` property method.
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
        with open(json_path, 'r') as f:
            data = json.load(f)
        return cls(**data)

    @classmethod
    def from_dict(cls, data: dict):
        return cls(**data)

    def __repr__(self):
        return 'Calibration()'

    def __str__(self):
        return 'Calibration()'

    @property
    def observations(
        self,
    ) -> xr.Dataset:
        """
        Load and process observational data based on
        `self.observation_config` dictionary. The dictionary must
        follow a specific structure to properly load the data.
        An example is given in the following:
        >>> [
        ...     {
        ...         'name': 'station_1',
        ...         'type': 'QO',
        ...         'timeseries': [
        ...             ('2020-01-01', 10.5),
        ...             ('2020-01-02', 12.3),
        ...         ],
        ...         'units': 'm3/s',
        ...         'computational_unit': 'subbasin',
        ...         'computational_unit_id': 14,
        ...         'freq': '1D',
        ...     },
        ...     ...
        ... ]

        - `type` refers to the observational equivalence of the model output.
           In this case, `QO` is a varibale name for routed streamflow output
           from the MESH model.
        - `location` is a dictionary containing the latitude and longitude
           of the observation point.
        - `timeseries` is an array-like object containing date-value pairs
           for the observational data.
        - `units` specifies the units of the observational data.
        - `freq` specifies the frequency of the observational data. The 
           value follows the pandas frequency strings (e.g., '1D' for
           daily, '1H' for hourly). For more details, refer to:
           https://pandas.pydata.org/pandas-docs/stable/user_guide/timeseries.html#dateoffset-objects

        Returns
        -------
        xarray.Dataset
            Processed observational data in a xarray.Dataset format.
        """
        # by default, enable converting units
        convert_units: bool = True

        entries = list(self._obs)

        # Collect per-entry parsed series and metadata
        per_entry_series: List[pd.Series] = []
        per_entry_time_index: List[pd.DatetimeIndex] = []
        per_entry_meta: List[Dict] = []

        for e in entries:
            ts = e.get("timeseries", [])
            if not ts:
                idx = pd.DatetimeIndex([])
                vals = np.array([], dtype=float)
            elif isinstance(ts, pd.Series):
                pass # do nothing
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
        global_time = _union_sorted_times(per_entry_time_index)

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
        if not isinstance(value, list | pd.Series):
            raise TypeError("`observations` must be a list of dictionaries or a pandas Series.")
        self._obs = value
        return

    def to_dict(self) -> dict:
        """Convert the object to a dictionary."""
        return self.__dict__

    def prepare(
        self,
        output_path: PathLike = None) -> None:
        """Prepare the calibration and model objects."""
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

        # 3. observation part
        self.observations.to_netcdf(os.path.join(
            output_path,
            'etc',
            'observations',
            'observations.nc'
        ))

        # 4. evaluation part
        self._eval()

        # 5. summarize FIAT inputs
        self._summarize_fiat_inputs(output_path=output_path)

        return

    def _eval(
        self,
    ) -> None:
        """Evaluate the model instance using the calibration software.
        This function is created to iteratively call model instances
        during the calibration process.
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
            'model_instance_path': '../../model/',
            'model_executable': self.model_config.get('executable'),
            'dates': self.calibration_config.get('dates'),
            'objective_functions': self.calibration_config.get('objective_functions'),
            'results_path': 'results',
            'output_files': [self.model.outputs],
            'observations_file': os.path.join(
                self.calibration_config.get('instance_path'),
                'etc',
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
            'parameters': {
                key: os.path.join('../templates', f'{key}.json')
                         for key in self.model.parameters.keys()},
            # `others` are static in each iteration but necessary to
            # be read by the script
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

        # destination path
        dest_path = os.path.join(
            self.calibration_config.get('instance_path'),
            'etc',
            'eval',
            'eval.py'
        )
        shutil.copy(src_path, dest_path)

        return

    def _summarize_fiat_inputs(
        self,
        output_path: PathLike = None,
    ) -> None:
        """Summarize all FIAT inputs into a single JSON file for
        record-keeping purposes.

        Parameters
        ----------
        output_path : PathLike
            The path where the summary JSON file will be saved.
        Returns
        -------
        None
        """
        summary_dict = {
            'calibration_software': self.calibration_software,
            'model_software': self.model_software,
            'calibration_config': self.calibration_config,
            'model_config': self.model_config,
            'observations': self._obs,
        }

        if output_path is None:
            output_path = self.calibration_config.get('instance_path')

        summary_path = os.path.join(
            output_path,
            'fiat_instance.json',
        )
        with open(summary_path, 'w') as f:
            json.dump(summary_dict, f, indent=4)

        return
