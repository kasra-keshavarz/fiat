"""
Templating utilities for the "MESH" instantiations.
"""
# built-in imports
import sys
import warnings
import os
import json

from typing import (
    Dict,
    Union,
    Optional,
    Sequence,
    List,
)
from pathlib import Path

# internal imports
from ..optimizer import OptimizerTemplateEngine
from . import default_dicts as DEFAULT_DICTS

# defining custom types
# PathLike type alias
if sys.version_info >= (3, 10):
    from typing import TypeAlias
    PathLike: TypeAlias = Union[str, Path]
else:
    PathLike = Union[str, Path]
# JsonType type alias
JSON: TypeAlias = dict[str, "JSON"] | list["JSON"] | str | int | float | bool | None

class OstrichTemplateEngine(OptimizerTemplateEngine):
    """
    A templating engine for generating configuration files
    for the MESH model using the OSTRICH calibration software.
    """

    def __init__(
        self,
        config: Dict,
        model: 'ModelBuilder', # type: ignore
    ) -> 'OstrichTemplateEngine':
        """
        Initialize the OstrichTemplateEngine class.
        """
        if config is None:
            raise ValueError("`config` dictionary must be provided.")

        super().__init__(
            config=config,
            calibration_software='ostrich',
            model=model,
        )
        # setting the jinja2 file template
        self.template = self.environment.get_template(
            self.model.model_software.lower() + '.jinja2')
        self.archive_template = self.environment.get_template(
            'archive.jinja2')
        # assigning global dictionaries for templating
        self.template.globals["default_dicts"] = DEFAULT_DICTS

        return

    def generate_optimizer_templates(
        self,
        output_path: PathLike,
        return_text: bool = False,
    ) -> str:
        """
        Generate a configuration file based on the provided parameters
        and a template file.

        Parameters
        ----------
        output_path : str
            The path where the generated configuration file will be saved.

        Returns
        -------
        None
        """
        self.template.globals["default_dicts"] = DEFAULT_DICTS

        # combining model information with the current config and supplying
        # the template with all necessary information
        info_dict = self.config.copy()
        # adding model 1) `parameters`, 2) `parameter_bounds`, and 
        # 3) `parameter_constraints`
        info_dict['parameters'] = self.model.templated_parameters
        info_dict['parameter_bounds'] = self.model.parameter_bounds
        info_dict['parameter_constraints'] = self.model.parameter_constraints

        # create content
        content = self.template.render(
            info=info_dict,
        )

        # save the `content` to the `output_path`
        self._create_dir(output_path) # assure it exists
        with open(os.path.join(output_path, 'ostIn.txt'), 'w') as f:
            f.write(content)

        # check to see if it is necessary to return the text
        if return_text:
            return content

        return

    def generate_parameter_templates(
        self,
        output_path,
        return_templates: bool = False,
    ) -> Optional[Sequence[JSON]]:
        """"Generate parameter templates for the model using
        the assumptions provided in OSTRICH calibration
        software.
        """
        objects: List[JSON] = []
        # The parameter templates are generated and stored
        # within the `model` instance. The values need
        # to be printed into `$OUTPUT_PATH/etc/templates/`
        # directory for OSTRICH to use them.
        for group, params in self.model.templated_parameters.items():
            # create directory for each parameter group
            group_path = os.path.join(
                output_path,
                'etc',
                'templates',
            )
            self._create_dir(group_path)

            # dump JSON files for each parameter group
            with open(
                os.path.join(
                    group_path,
                    f'{group}.json',
                ),
                'w',
            ) as f:
                json_obj = json.dumps(params, indent=4)
                f.write(json_obj)
            
            if return_templates:
                objects.append(params)

        if return_templates:
            return objects

        return

    def generate_etc_templates(
        self,
        output_path: PathLike,
    ) -> None:
        """
        Generate necessary `etc` templates for OSTRICH calibration and
        parts that are optional to be created, including archiving
        strategy.

        Parameters
        ----------
        output_path : PathLike
            The path where the generated `etc` templates will be saved.

        Returns
        -------
        None
        """
        # create the `etc` directory
        etc_path = os.path.join(
            output_path,
            'etc',
        )
        self._create_dir(etc_path)

        # creating `scripts` and `eval` directories within `etc`
        other_dirs = ['scripts', 'eval', 'observations', 'templates']
        for other_dir in other_dirs:
            self._create_dir(os.path.join(etc_path, other_dir))

        # create an archiving script
        archive_script_path = os.path.join(
            etc_path,
            'scripts',
            'archive.sh',
        )

        archive_content = self.archive_template.render(
            model=self.model.model_software.lower())
        with open(archive_script_path, 'w') as f:
            f.write(archive_content)

        return

    def _create_dir(self, path: PathLike) -> None:
        """Create directory if it does not exist."""
        # if `path` exists, give a warning and create the path nonetheless
        if os.path.exists(path):
            warnings.warn(f"The directory {path} already exists."
                          " Contents may be overwritten.", UserWarning)
        # create the directory
        os.makedirs(path, exist_ok=True)

        return