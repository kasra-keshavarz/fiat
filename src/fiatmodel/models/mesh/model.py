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
        # build MESH-specific required directories
        self.required_dirs = [
            'results',
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
        forcinglist_pattern = re.compile(r"^\s*FORCINGFILESLIST\s+([^\s#;]+)")
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
                            forcing_file_list = m.group(1).rstrip('\r\n')
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
            section_landcover_type = determine_gru_type(
                line=class_section['veg1'].splitlines()[0],
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

            # parse the sections -- hard-coded as there are no
            # other alternatives
            veg1_params = parse_class_veg1(
                veg_section=class_section['veg1'],
                gru_idx=section_landcover_type,
            )
            veg2_params = parse_class_veg2(
                veg_section=class_section['veg2'],
                gru_idx=section_landcover_type,
            )
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

            # make a list of parameters for easier literal unpacking inside
            # the gru_entry dictionary
            param_list = [
                veg1_params,
                veg2_params,
                hyd1_params,
                hyd2_params,
                soil_params,
                prog1_params,
                prog2_params,
                prog3_params,
            ]

            # make sure to make an exception for water-like land covers
            if 'water' in hyd2_params['mid'].lower():
                class_type = 'water'
            elif 'snow' in hyd2_params['mid'].lower():
                class_type = 'water'
            elif 'ice' in hyd2_params['mid'].lower():
                class_type = 'water'
            else:
                class_type = class_name_dict[section_landcover_type]

            # adding class type info
            gru_entry[idx] = {
                'class': class_type,
            }
            # adding parameters
            gru_entry[idx].update({k: v for d in param_list for k, v in d.items()})

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
        routing_df = pd.read_csv(StringIO(sections[2]), comment='#', sep='\s+', index_col=0, skiprows=1, header=None)
        routing_df.index = routing_df.index.str.lower()
        # we should return a list of values
        routing_dict = [v for v in routing_df.to_dict().values()]

        # and second, the hydrology dictionary
        hydrology_df = pd.read_csv(StringIO(sections[4]), comment='#', sep='\s+', index_col=0, skiprows=2, header=None)
        hydrology_df.index = hydrology_df.index.str.lower()
        # and we return a dictionary of this
        hydrology_dict = hydrology_df.to_dict()

        return routing_dict, hydrology_dict

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
            'case_entry': case_entry,
            'info_entry': info_entry,
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
                    # create the parameter name
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
        """
        # check whether the instance has been analyzed
        if not self.step_logger['analyze']:
            self.analyze()

        # given the parameter bounds in self.config['parameter_bounds'],
        # the necessary parameter dictionaries are templated and saved

        # initialize the `templated_parameters` dictionary
        self.templated_parameters = self.parameters.copy()

        # define parameter names that will be involved
        # in the calibration process
        for group_name, group in self.config['parameter_bounds'].items():
            # building the templated_parameters dictionary
            # for each parameter group in the `parameters` dictionary
            for unit in group.keys():
                # iterate over the computational units
                # update the values of parameters in each unit
                unit_params = group[unit]
                # input can be either a dictionary or a list
                for p in unit_params.keys():
                    if isinstance(self.parameters[group_name], dict):
                        # iterate over the parameters in the unit
                        if p in self.parameters[group_name][unit].keys():
                            # updating the target group dictionary
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
        # define parameter bounds
        self.parameter_bounds = self.config['parameter_bounds']

        return
