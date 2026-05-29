"""MESH model builder for calibration workflows.

Implements a concrete :class:`~fiatmodel.models.builder.ModelBuilder` for the
MESH hydrological model, including sanity checks, parameter analysis,
preparation of templated inputs, and staging of model artifacts.
"""
import pandas as pd
import xarray as xr

import re
import os
import shutil
import sys
import warnings
import copy

from typing import (
    Dict,
    Sequence,
    Union,
    List,
)
from datetime import (
    datetime,
    timedelta,
)

from pathlib import Path
from io import StringIO
from dateutil import parser

# internal imports
from ..builder import ModelBuilder
from .funcs import *

# custom types
# PathLike type alias for file system paths
if sys.version_info >= (3, 10):
    from typing import TypeAlias
    PathLike: TypeAlias = Union[str, Path]
else:
    PathLike = Union[str, Path]
# NameType type alias for parameter names
NameType = Union[str, int, float]

# useful functions for string formatting
def _rename_proxy(name: str) -> str:
    return name.replace('LAMN', 'LMN').replace('LAMX', 'LMX') + '_'

def _rename_eff(name: str) -> str:
    return name.replace('LAMX', 'LAMX_EFF')

class MESH(ModelBuilder):
    """Builder for the MESH calibration instantiation.

    Specializes the generic builder with MESH-specific file requirements,
    parameter parsing, forcing detection, and output configuration.

    Parameters
    ----------
    config : dict
        Configuration dictionary for the MESH instance (including
        ``instance_path`` and other paths/options).
    calibration_software : dict
        Calibration engine configuration; name is inferred upstream.
    fluxes : Sequence[str], optional
        Flux variables to be output and used in calibration.
    dates : Sequence[dict[str, str]] or None, optional
        List of window dictionaries with ``start`` and ``end`` ISO strings.

    Attributes
    ----------
    required_files : list[str]
        Files required to exist in the MESH instance directory.
    required_dirs : list[str]
        Directories required or created for a runnable instance.
    timestamp : str
        Time-stamp suffix used when creating backups.
    forcing_file : list[str]
        Absolute path(s) to forcing file(s) detected from run options.
    forcing_freq : str or None
        Inferred time-step frequency of forcing inputs.
    outputs : list[str]
        Expected output NetCDF files for selected fluxes.
    parameters : dict
        Assembled parameter structures (CLASS, hydrology, routing).
    others : dict
        Auxiliary metadata such as ``case_entry`` and ``info_entry``.

    Methods
    -------
    sanity_check()
        Validate required inputs and normalize forcing paths.
    analyze(cache=None)
        Build parameter structures and set expected outputs.
    prepare()
        Template parameters, bounds and constraints for calibration.
    computational_units
        Property returning counts of computational units by group.
    """

    _FORBIDDEN_LOG = frozenset({'clay1', 'clay2', 'clay3',
                                'sand1', 'sand2', 'sand3'})

    def __init__(
        self,
        config: Dict,
        calibration_software: Dict,
        fluxes: Sequence[str] = [],
        dates: Sequence[Dict[str, str]] | None = None,
        spinup: str | None = None,
    ) -> None:
        """Initialize the MESH builder with configuration and options.

        Parameters
        ----------
        config : dict
            MESH instance configuration including ``instance_path``.
        calibration_software : dict
            Calibration engine settings passed through to the base builder.
        fluxes : Sequence[str], optional
            Flux variables to output, by default ``[]``.
        dates : Sequence[dict[str, str]] or None, optional
            Calibration window(s) with ``start`` and ``end`` ISO strings.
        """
        # build the parent class
        super().__init__(
            config,
            calibration_software,
            model_software='mesh',
            fluxes=fluxes,
            dates=dates,
            spinup=spinup,
        )

        # build MESH-sepcific required files
        self.required_files = [
            'MESH_drainage_database.nc',
            'MESH_input_run_options.ini',
            'MESH_input_soil_levels.txt',
            'MESH_input_reservoir.txt',
            'MESH_input_streamflow.txt',
            'MESH_parameters.txt',
            'outputs_balance.txt',
            'MESH_parameters_CLASS.ini',
            'MESH_parameters_hydrology.ini',
            ]

        # if `executable` is provided in the config, we can also
        # add it to the required files
        if 'executable' in self.config:
            self.required_files.append(self.config['executable'])

        # build MESH-specific required directories
        self.required_dirs = [
            'results',
        ]
        # MESH optional files
        self.optional_files = [
            'MESH_parameters.nc',
        ]
        # time-stamp string for backups
        self.timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    def sanity_check(self) -> bool:
        """Perform sanity checks and normalize forcing configuration.

        Checks that all required files exist under ``config['instance_path']``
        and locates forcing file(s) via entries in ``MESH_input_run_options.ini``
        (``fname``, ``fpath`` or ``FORCINGFILESLIST``). Paths are rewritten to
        absolute, and a backup of modified files is created using ``timestamp``.

        Returns
        -------
        bool
            ``True`` if all checks pass and the instance is consistent.

        Raises
        ------
        FileNotFoundError
            If any required file or declared forcing file is missing.
        """
        # check self.instance_path and see if all files in `required_files` exist
        missing_files = []
        for file in self.required_files:
            if not os.path.isfile(os.path.join(self.config['instance_path'], file)):
                missing_files.append(file)

        # raise error if any required files are missing
        if missing_files:
            raise FileNotFoundError(
                f"The following required files are missing in the instance path "
                f"`{self.config['instance_path']}`: {', '.join(missing_files)}"
            )

        # check for forcing file(s) by first looking for a "fname" in the
        # `MESH_input_run_options.ini` file, or by looking for a "FORCINGFILESLIST"
        # entry in the same file
        run_options_path = os.path.join(
            self.config['instance_path'], 'MESH_input_run_options.ini'
        )

        # assign patterns to search in `run_options_path` file
        fname_pattern = re.compile(r"\bfname\s*=\s*([^ \t#;]+)")
        fpath_pattern = re.compile(r"\bfpath\s*=\s*([^ \t#;]+)")
        forcinglist_pattern = re.compile(r"^\s*FORCINGLIST\s+([^\s#;]+)")
        patterns_list = [
            ('fname', fname_pattern),
            ('fpath', fpath_pattern),
            ('FORCINGFILESLIST', forcinglist_pattern),
        ]

        with open(run_options_path, "r", encoding="utf-8") as f:
            for line in f:
                for pattern_name, pattern in patterns_list:
                    m = pattern.search(line)
                    if m:
                        # if fname= is provided
                        if pattern_name == 'fname':
                            forcing_file = m.group(1).rstrip('\r\n')
                            # assign self.forcing_file to the full path
                            forcing_file_path = os.path.join(
                                self.config['instance_path'], forcing_file + '.nc',
                            )
                            if not os.path.isfile(forcing_file_path):
                                raise FileNotFoundError(
                                    f"The forcing file {forcing_file} specified in "
                                    f"{run_options_path} is not found."
                                )
                            # turning into absolute path and assign to self.forcing_file
                            self.forcing_file = [os.path.abspath(forcing_file_path)]

                            # before making changes, back up the original run options file
                            backup_path = run_options_path + f'.bak_{self.timestamp }'
                            shutil.copy(run_options_path, backup_path) # no need to preserve metadata

                            # if `fname` is matched, we need to update that entry to be absolute path using `fpath`
                            # read the original file and replace the entry
                            with open(backup_path, 'r', encoding="utf-8") as fin, open(run_options_path, 'w', encoding="utf-8") as fout:
                                for line in fin:
                                    if pattern.search(line):
                                        m = pattern.search(line)
                                        start, end = m.span()
                                        new_line = line[:start] + f"fpath={self.forcing_file[0]}" + line[end:]
                                        if not new_line.endswith('\n'):
                                            new_line += '\n'
                                        fout.write(new_line)
                                    else:
                                        fout.write(line)

                        # if fpath= is provided
                        elif pattern_name == 'fpath':
                            forcing_file = m.group(1).rstrip('\r\n')

                            # make sure it's an absolute path
                            if not os.path.isabs(forcing_file):
                                forcing_file_path = os.path.join(
                                    self.config['instance_path'], forcing_file,
                                )
                            else:
                                forcing_file_path = forcing_file

                            if not os.path.isfile(forcing_file_path):
                                raise FileNotFoundError(
                                    f"The forcing file {forcing_file} specified in "
                                    f"{run_options_path} is not found."
                                )
                            # turning into absolute path and assign to self.forcing_file
                            self.forcing_file = [os.path.abspath(forcing_file_path)]

                            # before making changes, back up the original run options file
                            backup_path = run_options_path + f'.bak_{self.timestamp }'
                            shutil.copy(run_options_path, backup_path) # no need to preserve metadata

                            # if `fname` is matched, we need to update that entry to be absolute path using `fpath`
                            # read the original file and replace the entry
                            with open(backup_path, 'r', encoding="utf-8") as fin, open(run_options_path, 'w', encoding="utf-8") as fout:
                                for line in fin:
                                    if pattern.search(line):
                                        m = pattern.search(line)
                                        start, end = m.span()
                                        new_line = line[:start] + f"fpath={self.forcing_file[0]}" + line[end:]
                                        if not new_line.endswith('\n'):
                                            new_line += '\n'
                                        fout.write(new_line)
                                    else:
                                        fout.write(line)

                        # if FORCINGFILESLIST option is provided
                        elif pattern_name == 'FORCINGFILESLIST':
                            forcing_file_list = m.group(1).rstrip('\r\n') + '.txt'
                            # read the forcing file list and check if all files exist
                            with open(os.path.join(self.config['instance_path'], forcing_file_list), "r", encoding="utf-8") as f:
                                for line in f:
                                    forcing_file = line.strip()
                                    if not os.path.isfile(forcing_file):
                                        raise FileNotFoundError(
                                            f"The forcing file {forcing_file} listed in "
                                            f"{forcing_file_list} is not found."
                                        )
                            # assign self.forcing_file to the list of full absolute paths
                            self.forcing_file = [
                                os.path.abspath(os.path.join(self.config['instance_path'], line.strip()))
                                for line in open(os.path.join(self.config['instance_path'], forcing_file_list), "r", encoding="utf-8")
                            ]
                            # also add the forcing_file_list to the `self.required_files`
                            self.required_files.append(os.path.abspath(os.path.join(self.config['instance_path'], forcing_file_list)))
                            # the corresponding entry file (the file including 
                            # forcing data paths) to include the absolute paths;
                            # so no need to change the run options file itself
                            # before making changes, back up the original forcing file list
                            backup_path = os.path.join(
                                self.config['instance_path'],
                                forcing_file_list + f'.bak_{self.timestamp }'
                            )
                            shutil.copy(
                                os.path.join(self.config['instance_path'], forcing_file_list),
                                backup_path
                            ) # no need to preserve metadata
                            # now update the forcing file list to include absolute paths
                            with open(os.path.join(self.config['instance_path'], forcing_file_list), 'w', encoding="utf-8") as fout:
                                for f in self.forcing_file:
                                    fout.write(f"{f}\n")

                            # break out of the loops
                            break

                        else:
                            raise FileNotFoundError(
                                f"The required forcing file(s) not found."
                            )

        # check the timeseries frequency of the forcing file(s)
        # only checking one file is sufficient, as all forcing files
        # should have the same frequency
        freq = xr.infer_freq(
            xr.open_dataset(self.forcing_file[0]).time
        )
        self.forcing_freq = freq

        # make a backup of the original outputs_balance.txt file
        outputs_balance_path = os.path.join(
            self.config['instance_path'], 'outputs_balance.txt'
        )
        backup_outputs_balance_path = outputs_balance_path + f'.bak_{self.timestamp }'
        shutil.copy(outputs_balance_path, backup_outputs_balance_path) # no need to preserve metadata

        # create a new one and only print the fluxes that are
        # necessary for calibration
        with open(outputs_balance_path, 'w', encoding="utf-8") as fout:
            fout.write(
                "!MESH Outputs Balance File generate by FIAT\n"
                "!Only the necessary output variables for calibration are included here.\n"
                "!Format: variable_name  output_frequency nc\n"
            )
            # hard-coded necessary fluxes for MESH calibration
            necessary_fluxes = self.fluxes
            for flux_name in necessary_fluxes:
                fout.write(f"{flux_name.upper()}     {self.forcing_freq.upper()}   nc\n")

        # adjust the model executation dates, if provided
        if self.dates: # keys are `start` and `end`
            # calculate the julian dates of start and end dates
            earliest = min(parser.parse(d['start']) for d in self.dates)
            latest = max(parser.parse(d['end']) for d in self.dates)
            # subtracting (from earliest) and adding (to latest)
            # one time step to ensure the model runs for the full
            # duration specified by the user
            earliest = pd.Timestamp(earliest) - pd.tseries.frequencies.to_offset(self.forcing_freq)
            latest = pd.Timestamp(latest) + pd.tseries.frequencies.to_offset(self.forcing_freq)

            # one has to also consider spinup too
            if self.spinup:
                spinup_start = parser.parse(self.spinup)
                if spinup_start < earliest:
                    earliest = spinup_start

            # calculate the year, day_of_year, hour, minute
            # for both the `earliest` and `latest` dates
            earliest_comps = (
                earliest.timetuple().tm_year,
                earliest.timetuple().tm_yday,
                earliest.timetuple().tm_hour,
                earliest.timetuple().tm_min,
            )
            latest_comps = (
                latest.timetuple().tm_year,
                latest.timetuple().tm_yday,
                latest.timetuple().tm_hour,
                latest.timetuple().tm_min,
            )

            # make MESH-compliant date strings
            start_str = str(earliest_comps[0]) + \
                " " + \
                f"{earliest_comps[1]:03d}" + \
                spaces(earliest_comps[2]) + \
                str(earliest_comps[2]) + \
                spaces(earliest_comps[3]) + \
                str(earliest_comps[3])

            end_str = str(latest_comps[0]) + \
                " " + \
                f"{latest_comps[1]:03d}" + \
                spaces(latest_comps[2]) + \
                str(latest_comps[2]) + \
                spaces(latest_comps[3]) + \
                str(latest_comps[3])

            # read the original run options file and back it up
            replace_prefix_in_last_two_lines(
                path=run_options_path,
                replacements=(start_str, end_str),
                width=17,
            )

        # if we reach here, all checks passed, so return True
        return True

    def analyze(self, cache: PathLike = None) -> None:
        """Analyze configuration and populate model parameters and outputs.

        Parameters
        ----------
        cache : PathLike, optional
            Optional cache directory for analysis artifacts (currently unused).
        """
        # perform sanity checks
        self.sanity_check()

        # given that sanity checks are passed, we can define the output
        # files
        for f in self.fluxes:
            # FIXME: only netcdf files are currenlty support with MESH
            output_file = f"{f.upper()}_{self.forcing_freq.upper()}_GRD.nc"
            self.outputs.append(output_file)

        # analyze the CLASS file and build the parameter dictionaries
        # for MESH's specific parameter analysis functions, the `case_entry`
        # and `info_entry` dictionaries are also returned, but not used in
        # calibration process
        case_entry, info_entry, class_dict = self._analyze_mesh_class()

        # analyze hydrology and routing files and build the parameter dictionaries
        routing_dict, hydrology_dict = self._analyze_mesh_hydrology()

        # model's raw parameters dictionary
        # the keys are hard-coded and documented in the model-specific
        # MESH builder documentation
        self.parameters = {
            'class': class_dict,
            'hydrology': hydrology_dict,
            'routing': routing_dict,
        }

        self.others = {
            'case_entry': {
                'type': 'json',
                'data': case_entry,
            },
            'info_entry': {
                'type': 'json',
                'data': info_entry,
            },
            'parameters_ds': {
                'type': 'nc',
                'data': parse_parameters_nc(
                    os.path.join(self.config['instance_path'], 'MESH_parameters.nc')),
            },
        }

        # add the step logger entry
        self.step_logger['analyze'] = True

        return

    @property
    def computational_units(self) -> Dict[str, int]:
        """Counts of computational units per parameter group.

        Returns
        -------
        dict[str, int]
            Counts for each parameter group present after analysis.
        """
        if self.step_logger['analyze']:
            return {
                'class_dict': len(self.parameters['class_dict']),
                'hydrology_dict': len(self.parameters['hydrology_dict']),
                'routing_dict': len(self.parameters['routing_dict']),
            }
        else:
            raise RuntimeError(
                "The `analyze` method must be called before accessing "
                "the `computational_units` property."
            )

        return

    @property
    def parameter_constraints(self):
        """Hard-coded and user-extendable parameter constraints.

        The mathematical representations are documented in the MESH builder
        guide. A setter is provided to allow users to supply additional
        constraints.

        Returns
        -------
        dict
            Current constraints mapping by parameter group.
        """
        # define a list of parameters that need to be included in contraints
        # these are MESH-specific --- hard-coded values
        if isinstance(self._parameter_constraints, dict) and len(self._parameter_constraints) == 0:
            constraints_params_template = ['clay', 'sand']
            # and building invidiual parameters present in all MESH configurations
            constraint_params = []

            # default is assuming MESH has 3 soil layers -- hard-coded
            # FIXME: the 3 layer assumption should be revisited in future releases
            #        both in FIAT-specific MESH builder and MESHFlow package.
            for i in range(1, 4):
                # iterate over the parameter template values
                for p in constraints_params_template:
                    # create the parameter name: e.g., sand1, clay2, etc.
                    param_name = f"{p.lower()}{i}"
                    # append to the list
                    constraint_params.append(param_name)

            # calibration constraints for each class computation unit
            # FIXME: kind of hard-coded assumption that the `class` parameters
            #        are the only ones that need constraints. This should be
            #        revisited in future releases.
            calibration_constraints = {}

            for unit in self.parameter_bounds['class'].keys():
                # creating a set of parameters for the computational
                # unit to be calibrated
                calibrated_set = set(self.parameter_bounds['class'][unit].keys())

                # check whether any of `constrain_params` elements are available
                # in each computational unit's set of parameters
                match = [x for _, x in enumerate(constraint_params) if x in calibrated_set]

                # set it aside if match is found
                if match is not None:
                    calibration_constraints[unit] = match

            if self.step_logger['analyze']:
                # hard-coded parameter constraints for MESH model parameters
                # the keys are hard-coded and documented in the model-specific
                # MESH builder documentation
                self._parameter_constraints = {
                    'class': calibration_constraints,
                }

                # LAMN/LAMX ordered-pair handling:
                #   * Rename the templated names of every calibrated lamn/lamx
                #     entry to the ``_<U>LMN[suffix]_`` / ``_<U>LMX[suffix]_``
                #     convention (trailing underscore, mirroring the CLAY/SAND
                #     disambiguation trick). For case-4 ``lamx`` the stored name
                #     is instead overridden to ``_<U>LAMX_EFF[suffix]`` so that
                #     the model-facing value in ``class.json`` comes from the
                #     TiedParams block emitted in the Ostrich input.
                #   * Validate case-2 and case-3 bound-vs-actual relationships
                #     and raise a clear ``ValueError`` on violation.
                #   * Record only case-4 entries under the new
                #     ``'class_lam'`` key so the Jinja template knows which
                #     (unit, class) pairs need the 4-line TiedParams block.
                #   Note: case-4 is when both LAMX and LAMN of a computational unit
                #         are included for the calibration. In this case, the
                #         calibration mechanism needs to assure the following
                #         constraint is satisfied: LAMX >= LAMN.
                class_lam_constraints = self._compute_class_lam_constraints()
                if class_lam_constraints:
                    self._parameter_constraints['class_lam'] = class_lam_constraints

        return getattr(self, '_parameter_constraints')
    @parameter_constraints.setter
    def parameter_constraints(self, value: List[str]) -> None:
        """Set the parameter constraints mapping.

        Parameters
        ----------
        value : dict
            Constraints organized by parameter group and unit.
        """
        if not isinstance(value, dict):
            raise TypeError('`parameter_constraints` must be a dictionary')
        self._parameter_constraints = value

        return

    def prepare(self) -> None:
        """Prepare templated parameters, bounds, and constraints for calibration.

        Ensures analysis is complete, constructs ``templated_parameters`` by
        substituting calibratable names, and assigns bounds from configuration.

        If ``model_config`` contains a ``parameter_initial_values`` key, it is
        normalized (mixed-veg lists are collapsed to per-class dicts) and
        validated against ``parameter_bounds``.  The result is stored as
        ``self.parameter_initial_values`` and consumed by the calibration
        engine to set starting values in the optimizer input.
        """
        # check whether the instance has been analyzed
        if not self.step_logger['analyze']:
            self.analyze()

        # given the parameter bounds in self.config['parameter_bounds'],
        # the necessary parameter dictionaries are templated and saved

        # Normalize list-of-dicts bounds for mixed-veg GRUs:
        # Users may supply a list of dicts (each with a 'class' key) for
        # mixed-veg GRUs.  Normalize them into a single dict where veg
        # params map to {class_name: [min, max]} and GRU-level params map
        # to [min, max] (widest range across all dicts).
        normalized_bounds = copy.deepcopy(self.config['parameter_bounds'])

        # Validate every bounds entry once up front so errors are reported
        # before any templating work. This walker enforces three properties:
        #   1) the bounds entry itself is well-formed (delegated to
        #      ``parse_param_bounds``);
        #   2) logarithmic sampling is not requested for clay/sand soil
        #      parameters, which are tied via ``BeginTiedParams`` ratio
        #      constraints and must remain linear;
        #   3) each ``(group, unit, name)`` key resolves to a real parameter
        #      in ``self.parameters`` — this prevents a downstream Jinja2
        #      ``'NoneType' is not iterable`` crash when the user references
        #      a parameter that does not exist in the parsed model inputs.
        for _gname, _gdict in normalized_bounds.items():
            if not isinstance(_gdict, dict):
                continue
            for _unit, _unit_bounds in _gdict.items():
                if isinstance(_unit_bounds, list):
                    # Mixed-veg form: list of per-class dicts
                    for _veg_dict in _unit_bounds:
                        _cls = _veg_dict.get('class')
                        for _p, _bnd in _veg_dict.items():
                            if _p == 'class':
                                continue
                            self._walk_bounds(_gname, _unit, _p, _bnd,
                                         class_name=_cls)
                elif isinstance(_unit_bounds, dict):
                    for _p, _bnd in _unit_bounds.items():
                        if isinstance(_bnd, dict):
                            # per-class dict: {class_name: [min,max[,scale]]}
                            for _cls, _cbnd in _bnd.items():
                                self._walk_bounds(_gname, _unit, _p, _cbnd,
                                             class_name=_cls)
                        else:
                            self._walk_bounds(_gname, _unit, _p, _bnd)

        if 'class' in normalized_bounds:
            for unit, unit_bounds in normalized_bounds['class'].items():
                unit_data = self.parameters['class'][unit]
                if isinstance(unit_bounds, list) and not isinstance(unit_data, list):
                    raise ValueError(
                        f"GRU {unit} (class '{unit_data['class']}') is a "
                        f"single-vegetation GRU, but a list of mixed-vegetation "
                        f"bounds was provided. Use a single dictionary instead."
                    )
                if isinstance(unit_bounds, dict) and isinstance(unit_data, list):
                    veg_classes = [v['class'] for v in unit_data]
                    raise ValueError(
                        f"GRU {unit} is a mixed-vegetation GRU with classes "
                        f"{veg_classes}, but a single dictionary of bounds was "
                        f"provided. Use a list of dictionaries (one per "
                        f"vegetation class) instead."
                    )
                if isinstance(unit_bounds, list):
                    normalized_bounds['class'][unit] = \
                        normalize_mixed_veg_bounds(unit_bounds)

        # initialize the `templated_parameters` dictionary
        self.templated_parameters = self.parameters.copy()

        # define parameter names that will be involved
        # in the calibration process
        for group_name, group in normalized_bounds.items():
            # building the templated_parameters dictionary
            # for each parameter group in the `parameters` dictionary
            for unit in group.keys():
                # iterate over the computational units
                # update the values of parameters in each unit
                unit_params = group[unit]
                # input can be either a dictionary or a list
                for p, bounds in unit_params.items():
                    if isinstance(self.parameters[group_name], dict):
                        unit_data = self.parameters[group_name][unit]

                        if isinstance(unit_data, list):
                            # Mixed-veg GRU: unit_data is a list of veg dicts
                            if isinstance(bounds, dict):
                                # Veg-specific param: bounds is {class_name: [min, max]}
                                for i, veg_dict in enumerate(unit_data):
                                    class_type = veg_dict['class']
                                    if class_type in bounds and p in veg_dict:
                                        self.templated_parameters[group_name][unit][i][p] = \
                                            param_name_gen(unit, f"{p}_{class_type}")
                            else:
                                # GRU-level param: bounds is [min, max]
                                # Template in the last veg dict that contains it
                                for i in range(len(unit_data) - 1, -1, -1):
                                    if p in unit_data[i]:
                                        self.templated_parameters[group_name][unit][i][p] = \
                                            param_name_gen(unit, p)
                                        break

                        elif isinstance(unit_data, dict):
                            # Single-veg GRU: existing behavior
                            if p in unit_data:
                                self.templated_parameters[group_name][unit][p] = param_name_gen(unit, p)

                    elif isinstance(self.parameters[group_name], list):
                        if p in self.parameters[group_name][unit - 1].keys():
                            # updating the target group entry dictionary
                            self.templated_parameters[group_name][unit - 1][p] = param_name_gen(unit, p)

                    else:
                        raise TypeError(
                            "The parameter bounds for each computational unit "
                            "must be provided as a dictionary or a list."
                        )
        # --- normalize user-supplied initial values (optional) ---
        normalized_initial_values = copy.deepcopy(
            self.config.get('parameter_initial_values', {})
        )

        # Validate every initial value entry
        for _gname, _gdict in normalized_initial_values.items():
            if not isinstance(_gdict, dict):
                continue
            for _unit, _unit_values in _gdict.items():
                if isinstance(_unit_values, list):
                    # Mixed-veg form: list of per-class dicts
                    for _veg_dict in _unit_values:
                        _cls = _veg_dict.get('class')
                        for _p, _val in _veg_dict.items():
                            if _p == 'class':
                                continue
                            self._walk_initial_values(_gname, _unit, _p, _val,
                                                      class_name=_cls)
                elif isinstance(_unit_values, dict):
                    for _p, _val in _unit_values.items():
                        if isinstance(_val, dict):
                            # per-class dict: {class_name: value}
                            for _cls, _cval in _val.items():
                                self._walk_initial_values(_gname, _unit, _p, _cval,
                                                          class_name=_cls)
                        else:
                            self._walk_initial_values(_gname, _unit, _p, _val)

        if 'class' in normalized_initial_values:
            for unit, unit_values in normalized_initial_values['class'].items():
                unit_data = self.parameters['class'][unit]
                if isinstance(unit_values, list) and not isinstance(unit_data, list):
                    raise ValueError(
                        f"GRU {unit} (class '{unit_data['class']}') is a "
                        f"single-vegetation GRU, but a list of mixed-vegetation "
                        f"initial values was provided. Use a single dictionary instead."
                    )
                if isinstance(unit_values, dict) and isinstance(unit_data, list):
                    veg_classes = [v['class'] for v in unit_data]
                    raise ValueError(
                        f"GRU {unit} is a mixed-vegetation GRU with classes "
                        f"{veg_classes}, but a single dictionary of initial values was "
                        f"provided. Use a list of dictionaries (one per "
                        f"vegetation class) instead."
                    )
                if isinstance(unit_values, list):
                    normalized_initial_values['class'][unit] = \
                        normalize_mixed_veg_initial_values(unit_values)

        # Final validation: ensure structural consistency with bounds
        for _gname, _gdict in normalized_initial_values.items():
            if _gname not in normalized_bounds:
                raise ValueError(
                    f"Initial value group {_gname!r} not found in parameter_bounds."
                )
            for _unit, _unit_values in _gdict.items():
                if _unit not in normalized_bounds[_gname]:
                    raise ValueError(
                        f"Initial value unit {_unit!r} in group {_gname!r} "
                        f"not found in parameter_bounds."
                    )
                for _p, _val in _unit_values.items():
                    _bounds = normalized_bounds[_gname][_unit].get(_p)
                    if _bounds is None:
                        raise ValueError(
                            f"Initial value parameter {_p!r} in group {_gname!r}, "
                            f"unit {_unit} not found in parameter_bounds."
                        )
                    if isinstance(_val, dict) and not isinstance(_bounds, dict):
                        raise TypeError(
                            f"Initial value for parameter {_p!r} in group {_gname!r}, "
                            f"unit {_unit} is per-class, but bounds are not per-class."
                        )
                    if not isinstance(_val, dict) and isinstance(_bounds, dict):
                        raise TypeError(
                            f"Initial value for parameter {_p!r} in group {_gname!r}, "
                            f"unit {_unit} is a single value, but bounds are per-class."
                        )
                    if isinstance(_val, dict) and isinstance(_bounds, dict):
                        for _cls in _val.keys():
                            if _cls not in _bounds:
                                raise ValueError(
                                    f"Initial value class {_cls!r} for parameter {_p!r} "
                                    f"in group {_gname!r}, unit {_unit} not found in parameter_bounds."
                                )

        self.parameter_initial_values = normalized_initial_values

        # define parameter bounds (normalized form)
        self.parameter_bounds = normalized_bounds

        return

    def _walk_bounds(self, group_name, unit, name, bnd, class_name=None):
        lo, hi, scale = parse_param_bounds(bnd)
        if scale != 'none' and name in self._FORBIDDEN_LOG:
            raise ValueError(
                f"Parameter {name!r} (group {group_name!r}, unit {unit}) "
                f"participates in the clay/sand/silt ratio constraint "
                f"and cannot use scale {scale!r}; use 'none'."
            )
        self._check_param_exists(group_name, unit, name, class_name)

    def _walk_initial_values(self, group_name, unit, name, value, class_name=None):
        """Validate a single user-supplied initial value exists and is numeric."""
        self._check_param_exists(group_name, unit, name, class_name)
        if not isinstance(value, (int, float)):
            raise TypeError(
                f"Initial value for parameter {name!r} (group {group_name!r}, "
                f"unit {unit}) must be numeric, got {type(value).__name__}."
            )

    def _check_param_exists(self, group_name, unit, name, class_name=None):
        grp = self.parameters.get(group_name)
        if grp is None:
            raise ValueError(
                f"Parameter group {group_name!r} is not present in the "
                f"parsed model parameters; cannot apply bounds."
            )
        if isinstance(grp, dict):
            unit_data = grp.get(unit)
            if unit_data is None:
                raise ValueError(
                    f"Unit {unit!r} is not present in parameter group "
                    f"{group_name!r}; cannot apply bounds for {name!r}."
                )
            if isinstance(unit_data, dict):
                if name not in unit_data:
                    raise ValueError(
                        f"Parameter {name!r} not found in group "
                        f"{group_name!r}, unit {unit!r}. Available "
                        f"parameters: {sorted(unit_data.keys())}."
                    )
            elif isinstance(unit_data, list):
                if class_name is not None:
                    matching = [d for d in unit_data
                                if d.get('class') == class_name]
                    if not matching:
                        available = [d.get('class') for d in unit_data]
                        raise ValueError(
                            f"Vegetation class {class_name!r} not found "
                            f"in group {group_name!r}, unit {unit!r}. "
                            f"Available classes: {available}."
                        )
                    if not any(name in d for d in matching):
                        raise ValueError(
                            f"Parameter {name!r} not found in group "
                            f"{group_name!r}, unit {unit!r}, class "
                            f"{class_name!r}."
                        )
                else:
                    if not any(name in d for d in unit_data):
                        raise ValueError(
                            f"Parameter {name!r} not found in any "
                            f"vegetation entry of group {group_name!r}, "
                            f"unit {unit!r}."
                        )
        elif isinstance(grp, list):
            if not isinstance(unit, int) or unit < 1 or unit > len(grp):
                raise ValueError(
                    f"Unit {unit!r} out of range for list-form group "
                    f"{group_name!r} (expected 1..{len(grp)})."
                )
            unit_data = grp[unit - 1]
            if name not in unit_data:
                raise ValueError(
                    f"Parameter {name!r} not found in list-form group "
                    f"{group_name!r}, unit {unit!r}. Available "
                    f"parameters: {sorted(unit_data.keys())}."
                )

    def _compute_class_lam_constraints(self) -> Dict:
        """Classify LAMN/LAMX calibration cases and rename templated names.

        Walks every ``(unit, class?)`` pair in
        ``self.parameter_bounds['class']`` and classifies the ``lamn`` /
        ``lamx`` calibration entries into one of four cases:

        * Case 1 — neither ``lamn`` nor ``lamx`` calibrated: no-op.
        * Case 2 — only ``lamn`` calibrated: validate
          ``lamn.upper <= actual_lamx`` of that unit/class; on violation
          raise :class:`ValueError`.
        * Case 3 — only ``lamx`` calibrated: validate
          ``lamx.lower >= actual_lamn`` of that unit/class; on violation
          raise :class:`ValueError`.
        * Case 4 — both calibrated: record the ``(unit, class?)`` entry
          in the returned mapping so the OSTRICH TiedParams block is
          emitted for it.

        For every calibrated ``lamn`` / ``lamx`` entry (cases 2/3/4)
        this method also rewrites the corresponding string inside
        ``self.templated_parameters['class']`` in place:

        * ``_<U>LAMN[suffix]`` → ``_<U>LMN[suffix]_``
        * ``_<U>LAMX[suffix]`` → ``_<U>LMX[suffix]_``

        For case-4 entries, the ``lamx`` string is instead set to
        ``_<U>LAMX_EFF[suffix]`` so that the model-facing value in
        ``class.json`` is produced by the 4-line TiedParams block.

        Returns
        -------
        dict
            Mapping ``{unit: {class_name_or_None: True}}`` containing
            only case-4 entries. Empty dict if no unit/class is in
            case 4.
        """
        class_bounds = self.parameter_bounds.get('class', {}) or {}
        class_params = self.parameters.get('class', {}) or {}
        class_templated = self.templated_parameters.get('class', {}) or {}

        class_lam: Dict = {}

        for unit, unit_bounds in class_bounds.items():
            if not isinstance(unit_bounds, dict):
                continue
            unit_data = class_params.get(unit)
            unit_templated = class_templated.get(unit)

            lamn_raw = unit_bounds.get('lamn')
            lamx_raw = unit_bounds.get('lamx')

            # Build a list of (class_name_or_None, lamn_bnd, lamx_bnd,
            #                  actual_lamn, actual_lamx, target_dict)
            # where ``target_dict`` is the dict in templated_parameters
            # that holds the ``lamn`` / ``lamx`` keys we may rewrite.
            entries = []

            if isinstance(unit_data, dict):
                # Single-veg GRU
                entries.append((
                    None, # if cls is provided, it becomes confused with multi-vegetated GRU
                    lamn_raw if isinstance(lamn_raw, list) else None,
                    lamx_raw if isinstance(lamx_raw, list) else None,
                    unit_data.get('lamn'),
                    unit_data.get('lamx'),
                    unit_templated if isinstance(unit_templated, dict) else None,
                ))
            elif isinstance(unit_data, list):
                # Mixed-veg GRU: per-class resolution
                for i, veg_dict in enumerate(unit_data):
                    cls = veg_dict.get('class')
                    lamn_bnd = None
                    lamx_bnd = None
                    if isinstance(lamn_raw, dict):
                        lamn_bnd = lamn_raw.get(cls)
                    if isinstance(lamx_raw, dict):
                        lamx_bnd = lamx_raw.get(cls)
                    target = None
                    if (isinstance(unit_templated, list)
                            and i < len(unit_templated)
                            and isinstance(unit_templated[i], dict)):
                        target = unit_templated[i]
                    entries.append((
                        cls,
                        lamn_bnd,
                        lamx_bnd,
                        veg_dict.get('lamn'),
                        veg_dict.get('lamx'),
                        target,
                    ))
            else:
                continue

            for cls, lamn_bnd, lamx_bnd, actual_lamn, actual_lamx, target in entries:
                has_lamn = isinstance(lamn_bnd, (list, tuple)) and len(lamn_bnd) >= 2
                has_lamx = isinstance(lamx_bnd, (list, tuple)) and len(lamx_bnd) >= 2

                # Case 1: neither calibrated — nothing to do.
                if not has_lamn and not has_lamx:
                    continue

                # Case 2: only lamn — validate upper bound vs actual lamx.
                if has_lamn and not has_lamx:
                    if isinstance(actual_lamx, (int, float)) and lamn_bnd[1] > actual_lamx:
                        raise ValueError(
                            f"Invalid `lamn` calibration range for GRU "
                            f"{unit!r}"
                            + (f" (class {cls!r})" if cls is not None else "")
                            + f": upper bound {lamn_bnd[1]} exceeds the "
                            f"actual LAMX value {actual_lamx}. Reduce the "
                            f"`lamn` upper bound so that lamn <= lamx is "
                            f"guaranteed during sampling."
                        )

                # Case 3: only lamx — validate lower bound vs actual lamn.
                if has_lamx and not has_lamn:
                    if isinstance(actual_lamn, (int, float)) and lamx_bnd[0] < actual_lamn:
                        raise ValueError(
                            f"Invalid `lamx` calibration range for GRU "
                            f"{unit!r}"
                            + (f" (class {cls!r})" if cls is not None else "")
                            + f": lower bound {lamx_bnd[0]} is below the "
                            f"actual LAMN value {actual_lamn}. Raise the "
                            f"`lamx` lower bound so that lamn <= lamx is "
                            f"guaranteed during sampling."
                        )

                # Rename the templated proxy names for any calibrated
                # lamn/lamx entry (cases 2, 3, 4).
                if target is not None:
                    if has_lamn and isinstance(target.get('lamn'), str):
                        target['lamn'] = _rename_proxy(target['lamn'])
                    if has_lamx and isinstance(target.get('lamx'), str):
                        target['lamx'] = _rename_proxy(target['lamx'])

                # Case 4: both calibrated — record + override lamx with EFF.
                if has_lamn and has_lamx:
                    # if there is an overlap, then proceed with renaming
                    # and recording; otherwise, proceed as normal
                    if lamn_bnd[1] > lamx_bnd[0]:
                        class_lam.setdefault(unit, {})[cls] = True
                        if target is not None and isinstance(target.get('lamx'), str):
                            # target['lamx'] is already the renamed
                            # ``_<U>LMX[suffix]_``; swap it for the EFF name
                            # derived from the ORIGINAL param_name_gen form.
                            # Reconstruct the suffix from the renamed string
                            # by stripping the leading ``_<unit>LMX`` and the
                            # trailing ``_``: what's left is the suffix
                            # (possibly empty for single-veg).
                            renamed = target['lamx']
                            prefix = '_' + str(unit) + 'LMX'
                            assert renamed.startswith(prefix) and renamed.endswith('_')
                            suffix = renamed[len(prefix):-1]
                            target['lamx'] = '_' + str(unit) + 'LAMX_EFF' + suffix

        return class_lam

    def _copy_minimum_files(self, dest_path: str) -> None:
        """Copy the minimum required files to a destination path.

        Parameters
        ----------
        dest_path : str
            Destination directory where required files are copied.

        Raises
        ------
        FileNotFoundError
            If any required file is missing in the source instance path.
        """
        for file in self.required_files:
            src_file = os.path.join(self.config['instance_path'], file)
            dest_file = os.path.join(dest_path, file)
            if os.path.isfile(src_file):
                shutil.copy2(src_file, dest_file)
            else:
                raise FileNotFoundError(
                    f"The required file {file} is not found in the instance path "
                    f"{self.config['instance_path']}."
                )
        return

    def _analyze_mesh_class(self) -> Dict:
        """Analyze CLASS file and construct parameter structures.

        Parses ``MESH_parameters_CLASS.ini`` into multiple sections and builds
        structures required for templating.

        Notes
        -----
        Future releases may also use ``MESH_parameters.txt`` and
        ``MESH_parameters.nc``.

        Returns
        -------
        tuple
            ``(case_entry, info_entry, gru_entry)`` where entries are dicts
            keyed per MESH/CLASS semantics.
        """
        # two necessary paths for the analysis
        class_file = os.path.join(
            self.config['instance_path'], 'MESH_parameters_CLASS.ini'
        )

        # read the MESH/CLASS file
        text = Path(class_file).read_text(encoding="utf-8")

        # Split where there is at least one completely blank
        # line (possibly with spaces)
        sections = re.split(r'\r?\n\s*\r?\n', text.strip())

        # first section is typically the information section
        # the middle sections are CLASS computational unit blocks, each
        #     containing vegetation, soil, hydrology, and prognostic parameters
        # the last section are the dates that should not be processed and 
        #     its content does not matter for the analysis

        # building dictionaries out of the first section needed for 
        # MESHFLOW's `meshflow.utility.render_class_template` function
        info_entry, case_entry = \
            parse_class_meta_data(sections[0])

        # create an empty gru_entry dictionary to be further
        # populated by the following iterative loop
        gru_entry = {}

        # iterating over the sections until the last one
        for idx, section in enumerate(sections[1:-1], start=1):
            # divide the section into a dictionary of sections
            class_section = class_section_divide(section=section)

            # determine GRU type, based on CLASS assumptions:
            #    1. needleleaf forest
            #    2. broadleaf forest
            #    3. cropland
            #    4. grassland
            #    5. urban, barren land, or imprevious area
            gru_indices = determine_gru_type(
                line=class_section['veg1'].splitlines()[0],
                fallback_line=class_section['veg1'].splitlines()[1],
            )
            # based on the number extracted above, we can name the
            # GRU class
            class_name_dict = {
                1: "needleleaf",
                2: "broadleaf",
                3: "crop",
                4: "grassland",
                5: "urban",
            }

            # parse shared (non-veg) sections -- these are the same
            # regardless of vegetation type
            hyd1_params = parse_class_hyd1(
                hyd_line=class_section['hyd1'],
            )
            hyd2_params = parse_class_hyd2(
                hyd_line=class_section['hyd2'],
            )
            soil_params = parse_class_soil(
                soil_section=class_section['soil'],
            )
            prog1_params = parse_class_prog1(
                prog_line=class_section['prog1'],
            )
            prog2_params = parse_class_prog2(
                prog_line=class_section['prog2'],
            )
            prog3_params = parse_class_prog3(
                prog_line=class_section['prog3'],
            )

            # shared (non-veg) params collected for reuse
            shared_params = {}
            for d in [hyd1_params, hyd2_params, soil_params,
                      prog1_params, prog2_params, prog3_params]:
                shared_params.update(d)
            # ``mid_id`` is a GRU identifier (used as the outer key of
            # ``gru_entry`` below), not a CLASS parameter. Drop it from
            # ``shared_params`` so it is not emitted inside each GRU's
            # inner dict in ``class.json`` (meshflow's
            # ``_extract_class_params`` rejects keys absent from
            # ``default_CLASS_lines.json``).
            shared_params.pop('mid_id', None)

            # determine water-like override from the MID descriptor
            is_water = any(
                kw in hyd2_params['mid'].lower()
                for kw in ('water', 'snow', 'ice')
            )

            if len(gru_indices) == 1:
                # Single vegetation type -- existing behavior
                gru_idx = gru_indices[0]

                veg1_params = parse_class_veg1(
                    veg_section=class_section['veg1'],
                    gru_idx=gru_idx,
                )
                veg2_params = parse_class_veg2(
                    veg_section=class_section['veg2'],
                    gru_idx=gru_idx,
                )

                if is_water:
                    class_type = 'water'
                else:
                    class_type = class_name_dict[gru_idx]

                mid_id = hyd2_params['mid_id']
                gru_entry[mid_id] = {'class': class_type}
                gru_entry[mid_id].update(veg1_params)
                gru_entry[mid_id].update(veg2_params)
                gru_entry[mid_id].update(shared_params)

            else:
                # Mixed vegetation type -- produce a list of dicts,
                # one per non-zero vegetation component. MESHFlow's
                # render_class_template expects this format.
                veg_dicts = []
                for gru_idx in gru_indices:
                    veg1_p = parse_class_veg1(
                        veg_section=class_section['veg1'],
                        gru_idx=gru_idx,
                    )
                    veg2_p = parse_class_veg2(
                        veg_section=class_section['veg2'],
                        gru_idx=gru_idx,
                    )

                    if is_water:
                        class_type = 'water'
                    else:
                        class_type = class_name_dict[gru_idx]

                    combined = {'class': class_type}
                    combined.update(veg1_p)
                    combined.update(veg2_p)
                    combined.update(shared_params)
                    veg_dicts.append(combined)

                gru_entry[hyd2_params['mid_id']] = veg_dicts

        return case_entry, info_entry, gru_entry

    def _analyze_mesh_hydrology(self) -> Dict:
        """Analyze hydrology and routing components.

        Returns
        -------
        tuple
            ``(routing_dict, hydrology_dict)`` derived from hydrology config.
        """
        # extract sections from the hydrology file
        sections = hydrology_section_divide(
            os.path.join(self.config['instance_path'], 'MESH_parameters_hydrology.ini')
        )

        # first, the routing dictionary
        try:
            routing_df = pd.read_csv(StringIO(sections[2]), comment='#', sep='\s+', index_col=0, skiprows=1, header=None)
            routing_df.index = routing_df.index.str.lower()

            # we should return a list of values
            routing_dict = [v for v in routing_df.to_dict().values()]

        except pd.errors.EmptyDataError:
            warnings.warn(f"The routing section in MESH_parameters_hydrology.ini"
                          " is empty. Reading `MESH_parameters.nc` file.")
            routing_ds = xr.open_dataset(os.path.join(self.config['instance_path'], 'MESH_parameters.nc'))
            routing_df = routing_ds[['flz', 'pwr']].to_dataframe().T.to_dict()

            routing_dict = [v for v in routing_df.values()]

        # and second, the hydrology dictionary
        # parse the MID header line (prefixed with '!') to use as column keys
        hydrology_lines = sections[4].strip().splitlines()
        mid_columns = None
        for line in hydrology_lines:
            if line.strip().startswith('!'):
                mid_columns = [int(v) for v in line.strip().lstrip('!').split()]
                break

        hydrology_df = pd.read_csv(StringIO(sections[4]), comment='#', sep='\s+', index_col=0, skiprows=2, header=None)
        hydrology_df.index = hydrology_df.index.str.lower()

        if mid_columns is not None:
            hydrology_df.columns = mid_columns

        # and we return a dictionary of this
        hydrology_dict = hydrology_df.to_dict()

        # if the `MESH_parameters.nc` file exists, we can also
        # read the hydrology parameters from there


        return routing_dict, hydrology_dict
