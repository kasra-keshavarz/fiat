"""Templating utilities for Ostrich workflows.

Provides :class:`OstrichTemplateEngine`, a concrete implementation of
``OptimizerTemplateEngine`` specialized for the hydrological models
calibrated by the Ostrich optimization engine. It renders optimizer
configuration, parameter templates, model inputs, and auxiliary assets
required for evaluation.

Notes
-----
- This engine expects model adapters to expose parameter metadata (e.g.,
  ``templated_parameters``, ``parameter_bounds``, and
  ``parameter_constraints``) and model instance paths.
- Paths are treated as path-like objects (``str`` or :class:`pathlib.Path`).
"""
# built-in imports
import sys
import shutil
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
    """Templating engine for MESH calibrated with Ostrich.

    Subclass of :class:`~fiatmodel.calibration.optimizer.OptimizerTemplateEngine`
    that renders all artifacts needed by the Ostrich backend to evaluate the
    MESH model.

    Attributes
    ----------
    template : object
        Compiled Jinja2 template for the model-specific optimizer input
        (e.g., ``mesh.jinja2``). Type is the internal Jinja2 template object.
    archive_template : object
        Compiled Jinja2 template used to generate an archive script.
    environment : :class:`jinja2.Environment`
        Inherited from the base class; configured to the Ostrich template path.
    model : ModelBuilder
        Inherited model adapter providing parameters and required files.
    config : dict
        Inherited calibration configuration dictionary.

    Methods
    -------
    generate_optimizer_templates(output_path, return_text=False)
        Render and write the optimizer input file (e.g., ``ostIn.txt``).
    generate_parameter_templates(output_path, return_templates=False)
        Write grouped parameter JSON templates under ``etc/templates``.
    generate_etc_templates(output_path)
        Create auxiliary directories and scripts under ``etc/``.
    generate_model_templates(output_path)
        Stage required model files and directories under ``model/``.
    generate_obs_templates(output_path)
        Create the ``observations/`` directory used by calibration runs.
    """

    def __init__(
        self,
        config: Dict,
        model: 'ModelBuilder',  # type: ignore
    ) -> None:
        """Construct the Ostrich templating engine.

        Parameters
        ----------
        config : dict
            Calibration configuration dictionary consumed by the templates.
        model : ModelBuilder
            Model adapter instance for MESH providing parameters and paths.

        Returns
        -------
        OstrichTemplateEngine
            The initialized instance (standard Python behavior returns ``None``).

        Raises
        ------
        ValueError
            If ``config`` is not provided.
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
    ):
        """Render the optimizer input file (e.g., ``ostIn.txt``).

        Parameters
        ----------
        output_path : PathLike
            Directory where the optimizer input will be written.
        return_text : bool, default ``False``
            When ``True``, return the rendered text instead of only writing it.

        Returns
        -------
        str or None
            Rendered content if ``return_text`` is ``True``; otherwise ``None``.
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
    ):
        """Generate and persist parameter group templates.

        Parameters
        ----------
        output_path : PathLike
            Base output directory under which ``etc/templates`` will be created.
        return_templates : bool, default ``False``
            When ``True``, return the in-memory JSON-like objects written.

        Returns
        -------
        Sequence[JSON] or None
            Sequence of parameter group objects when ``return_templates`` is
            ``True``; otherwise ``None``.
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
        """Generate auxiliary assets under ``etc/``.

        Creates directories such as ``etc/scripts``, ``etc/eval``, and
        ``etc/templates``. Renders an ``archive.sh`` script and writes any
        additional ``others`` JSON files provided by the model adapter.

        Parameters
        ----------
        output_path : PathLike
            Base output directory where ``etc`` will be created.
        """
        # create the `etc` directory
        etc_path = os.path.join(
            output_path,
            'etc',
        )
        self._create_dir(etc_path)

        # creating `scripts` and `eval` directories within `etc`
        other_dirs = ['scripts', 'eval', 'templates']
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

        # make sure the script is executable
        os.chmod(archive_script_path, 0o755)

        # if `others` attribute is populated (not an empty dictionary)
        if len(self.model.others) > 0:
            for group, params in self.model.others.items():
                # dump JSON files for each parameter group
                with open(
                    os.path.join(
                        output_path,
                        'etc',
                        'templates',
                        f'{group}.json',
                    ),
                    'w',
                ) as f:
                    json_obj = json.dumps(params, indent=4)
                    f.write(json_obj)

        return

    def generate_model_templates(
        self,
        output_path: PathLike,
    ) -> None:
        """Stage model files required for evaluation.

        Copies required files and directories from the model instance path to
        ``<output_path>/model``. Forcing files are intentionally not copied to
        avoid data duplication.

        Parameters
        ----------
        output_path : PathLike
            Base output directory where the ``model`` directory will be created.
        """
        # copy the required files to `output_path/model/`
        model_output_path = os.path.join(
            output_path,
            'model',
        )
        self._create_dir(model_output_path)

        # copying required files---note that forcing files are not copied
        # and are not included in `self.mode.required_files` object on
        # purpose
        for file in self.model.required_files:
            shutil.copy(
                os.path.join(self.model.config['instance_path'], file),
                model_output_path,
            )

        # if there are required directories, copy them as well
        for dir in self.model.required_dirs:
            shutil.copytree(
                os.path.join(self.model.config['instance_path'], dir),
                os.path.join(model_output_path, dir),
                dirs_exist_ok=True,
            )

        return

    def generate_obs_templates(
        self,
        output_path: PathLike,
    ) -> None:
        """Prepare observation directory used by the calibration run.

        Parameters
        ----------
        output_path : PathLike
            Base output directory under which ``observations`` will be created.
        """
        # create the `etc/observations/` directory
        obs_path = os.path.join(
            output_path,
            'observations',
        )
        self._create_dir(obs_path)

        return