"""Module for building a MESH calibration instantiation."""
import pandas as pd

import re
import os
import shutil
import sys

from typing import (
    Dict,
    Union,
    List,
)
from pathlib import Path
from io import StringIO

# custom types
# PathLike type alias for file system paths
if sys.version_info >= (3, 10):
    from typing import TypeAlias
    PathLike: TypeAlias = Union[str, Path]
else:
    PathLike = Union[str, Path]
# NameType type alias for parameter names
NameType = Union[str, int, float]

# MESH-specific templating engine
from .funcs import *

# internal imports
from fiatmodel.models.builder import Builder

class MESH(Builder):
    """Build the MESH calibration instantiation."""

    def __init__(self, config: Dict) -> None:
        # build the parent class
        super().__init__(config)

        # build MESH-sepcific required files
        self.required_files = [
            'MESH_drainage_database.nc',
            'MESH_input_run_options.ini',
            'MESH_input_soil_levels.txt',
            'MESH_input_reservoir.txt',
            'MESH_parameters.txt',
            'outputs_balance.txt',
            'MESH_parameters_CLASS.ini',
            'MESH_parameters_hydrology.ini',
            ]

        # step logger for the MESH builder
        self.step_logger = {
            'analyze': False,
        }

    def sanity_check(self) -> bool:
        """Perform sanity checks on the configured MESH instance."""
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
        try:
            # see if a single forcing file is specified
            pattern = re.compile(r"\bfname\s*=\s*([^ \t#;]+)")
            with open(run_options_path, "r", encoding="utf-8") as f:
                for line in f:
                    m = pattern.search(line)
                    if m:
                        forcing_file = m.group(1)
                        # assign self.forcing_file to the full path
                        forcing_file_path = os.path.join(
                            self.config['instance_path'], forcing_file + '.nc',
                        )
                        if not os.path.isfile(forcing_file_path):
                            raise FileNotFoundError(
                                f"The forcing file {forcing_file} specified in "
                                f"{run_options_path} is not found."
                            )
                        self.forcing_file = [forcing_file_path]
                        break

        except:
            try:
                # see if multiple forcing files are specified within the model instance
                pattern = re.compile(r"^\s*FORCINGFILESLIST\s+([^\s#;]+)")
                with open(run_options_path, "r", encoding="utf-8") as f:
                    for line in f:
                        m = pattern.search(line)
                        if m:
                            forcing_file_list = m.group(1)
                            # read the forcing file list and check if all files exist
                            with open(os.path.join(self.config['instance_path'], forcing_file_list), "r", encoding="utf-8") as f:
                                for line in f:
                                    forcing_file = line.strip()
                                    if not os.path.isfile(forcing_file):
                                        raise FileNotFoundError(
                                            f"The forcing file {forcing_file} listed in "
                                            f"{forcing_file_list} is not found."
                                        )
                            # break out of the loop
                            # assign self.forcing_file to the list of full paths
                            self.forcing_file = [
                                os.path.join(self.config['instance_path'], line.strip())
                                for line in open(os.path.join(self.config['instance_path'], forcing_file_list), "r", encoding="utf-8")
                            ]
                            break

            except:
                raise FileNotFoundError(
                    f"The required forcing file(s) not found."
                )
        # if we reach here, all checks passed, so return True
        return True

    def _copy_minimum_files(self, dest_path: str) -> None:
        """Copy the minimum required files to a new instance path."""
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
        """Build parameter dictionary from existing
        `MESH_parameters_CLASS.ini` files.

        FIXME: in future `model.py` recipe release of this model, the
               `MESH_parameters.txt` and `MESH_parameters.nc` files can also
               be used to build the parameter dictionary.

        Analyze the CLASS file and return a dictionary containing the parsed sections.

        Parameters
        ----------
        class_file : Union[PathLike | str]
            The path to the CLASS file to be analyzed.

        Returns
        -------
        Tuple[Dict[str, Union[Dict, str]]]
            A dictionary containing the parsed sections of the CLASS file.
        """
        # two necessary paths for the analysis
        class_file = os.path.join(
            self.config['instance_path'], 'MESH_parameters_CLASS.ini'
        )
        hydrology_file = os.path.join(
            self.config['instance_path'], 'MESH_parameters_hydrology.ini'
        )
        # read the MESH/CLASS file
        text = Path(class_file).read_text(encoding="utf-8")

        # Split where there is at least one completely blank line (possibly with spaces)
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
        """
        Analyze the hydrology components of the MESH model.
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
        """Initialize the MESH model calibration builder instance."""
        # perform sanity checks
        self.sanity_check()

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
            'class_dict': class_dict,
            'hydrology_dict': hydrology_dict,
            'routing_dict': routing_dict,
        }

        # add the step logger entry
        self.step_logger['analyze'] = True

        return

    @property
    def computational_units(self) -> Dict[str, int]:
        """Return a dictionary with the number of computational units
        for each element in the `parameters` dictionary object"""
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
        """Hard-coded parameter constraints for MESH model parameters.
        The mathematical representation of these constraints are 
        """
        # define a list of parameters that need to be included in contraints
        # these are MESH-specific --- hard-coded values
        constraints_params_template = ['clay', 'sand']
        # and building invidiual parameters present in all MESH configurations
        constraint_params = []

        # default is assuming MESH has 3 soil layers
        for i in range(1, 4):
            # iterate over the parameter template values
            for p in constraints_params_template:
                # create the parameter name
                param_name = f"{p.lower()}{i}"
                # append to the list
                constraint_params.append(param_name)

        if self.step_logger['analyze']:
            # hard-coded parameter constraints for MESH model parameters
            # the keys are hard-coded and documented in the model-specific
            # MESH builder documentation
            return {
                'class_dict': constraint_params,
            }
        else:
            raise RuntimeError(
                "The `analyze` method must be called before accessing "
                "the `parameter_constraints` property."
            )

        return

    def build(
        self,
        save_path: PathLike = None) -> None:
        """Build the MESH calibration workflow.

        In this part, the bounds are taken into account and the necessary
        parameter dictionaries are templated.

        Also, if model has not been analyzed yet, it will be analyzed first."""
        # check whether the instance has been analyzed
        if not self.step_logger['analyze']:
            self.analyze()

        # if no save_path is provided, raise an exception
        if save_path is None:
            raise RuntimeError("A valid `save_path` must be provided"
                               " to build the MESH model instance.")

        # given the parameter bounds in self.config['parameter_bounds'],
        # the necessary parameter dictionaries are templated and saved

        # initialize the `templated_parameters` dictionary
        self.templated_parameters = self.parameters.copy()

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
                            self.templated_parameters[group_name][unit][p] = unit_params[p]

                    elif isinstance(self.parameters[group_name], list):
                        if p in self.parameters[group_name][unit - 1].keys():
                            # updating the target group entry dictionary
                            self.templated_parameters[group_name][unit - 1][p] = unit_params[p]

                    else:
                        raise TypeError(
                            "The parameter bounds for each computational unit "
                            "must be provided as a dictionary or a list."
                        )

        return
