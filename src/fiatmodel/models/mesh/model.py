"""Module for building a MESH calibration instantiation."""
import pandas as pd

import re
import os
import shutil
import sys

from typing import (
    Dict,
    Union,
)
from pathlib import Path
from io import StringIO

# custom types
if sys.version_info >= (3, 10):
    from typing import TypeAlias
    PathLike: TypeAlias = Union[str, Path]
else:
    PathLike = Union[str, Path]

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
            'output_balance.txt',
            'MESH_parameters_CLASS.ini',
            'MESH_parameters_hydrology.ini',
            ]

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
                f"{self.config['instance_path']}: {', '.join(missing_files)}"
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

    def _analyze_mesh_class(
        self,
    ) -> Dict:
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

        def init(self, cache: PathLike = None) -> None:
            """Initialize the MESH model calibration builder instance."""
            # perform sanity checks
            self.sanity_check()


            return
