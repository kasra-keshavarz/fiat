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
import math
import random

from typing import (
    Dict,
    Union,
    Optional,
    Sequence,
    List,
)
from pathlib import Path
from datetime import datetime

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
    """Templating engine for calibration using Ostrich.

    Subclass of :class:`~fiatmodel.calibration.optimizer.OptimizerTemplateEngine`
    that renders all artifacts needed by the Ostrich backend to evaluate the
    hydrological model.

    Attributes
    ----------
    template : object
        Compiled Jinja2 template for the model-specific optimizer input
        (e.g., ``mesh.jinja2`` for MESH). Type is the internal Jinja2
        template object.
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
            Model adapter instance for model providing parameters and paths.

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

    @staticmethod
    def _draw_initial(bounds):
        """Draw a single random initial value and format it.

        Parameters
        ----------
        bounds : list or tuple
            ``[min, max, scale]`` or ``[min, max]``.

        Returns
        -------
        str
            Formatted initial value.
        """
        min_val = float(bounds[0])
        max_val = float(bounds[1])
        scale = bounds[2] if len(bounds) > 2 else 'none'

        if min_val == max_val:
            val = min_val
        elif scale in ('log10', 'ln'):
            if scale == 'log10':
                val = 10 ** random.uniform(math.log10(min_val), math.log10(max_val))
            else:  # ln
                val = math.exp(random.uniform(math.log(min_val), math.log(max_val)))
        else:
            val = random.uniform(min_val, max_val)

        if scale in ('log10', 'ln'):
            return f"{val:.6e}"
        else:
            return f"{val:.6f}"

    @staticmethod
    def _format_initial_value(value, bounds):
        """Format a user-provided initial value and validate it against bounds.

        Parameters
        ----------
        value : int, float, or str
            User-supplied initial value.
        bounds : list or tuple
            ``[min, max, scale]`` or ``[min, max]``.

        Returns
        -------
        str
            Formatted initial value.

        Raises
        ------
        ValueError
            If the value is outside the bounds [min, max].
        TypeError
            If the value cannot be converted to float.
        """
        min_val = float(bounds[0])
        max_val = float(bounds[1])
        scale = bounds[2] if len(bounds) > 2 else 'none'

        try:
            val = float(value)
        except (TypeError, ValueError) as exc:
            raise TypeError(
                f"Initial value {value!r} cannot be converted to float."
            ) from exc

        if not (min_val <= val <= max_val):
            raise ValueError(
                f"Initial value {val} is outside the bounds "
                f"[{min_val}, {max_val}] (scale={scale!r})."
            )

        if scale in ('log10', 'ln'):
            return f"{val:.6e}"
        else:
            return f"{val:.6f}"

    def _generate_initial_values(self, bounds_dict, seed=None,
                                  initial_values_dict=None):
        """Generate initial values mirroring ``parameter_bounds`` structure.

        Parameters
        ----------
        bounds_dict : dict
            Nested dict matching ``parameter_bounds``.
        seed : int or None
            Optional random seed for reproducibility.
        initial_values_dict : dict or None
            Optional nested dict of user-supplied initial values with the same
            structure as ``bounds_dict``. Missing entries fall back to random.

        Returns
        -------
        dict
            Nested dict of formatted initial-value strings.
        """
        if seed is not None:
            random.seed(seed)

        if initial_values_dict is None:
            initial_values_dict = {}

        # Quick validation: ensure no extra keys in initial_values_dict
        for param_group, user_group in initial_values_dict.items():
            if param_group not in bounds_dict:
                raise ValueError(
                    f"Initial value group {param_group!r} not found in parameter_bounds."
                )
            for unit, user_unit in user_group.items():
                if unit not in bounds_dict[param_group]:
                    raise ValueError(
                        f"Initial value unit {unit!r} in group {param_group!r} "
                        f"not found in parameter_bounds."
                    )
                for name, user_param in user_unit.items():
                    bounds = bounds_dict[param_group][unit].get(name)
                    if bounds is None:
                        raise ValueError(
                            f"Initial value parameter {name!r} in group "
                            f"{param_group!r}, unit {unit} not found in parameter_bounds."
                        )
                    if isinstance(user_param, dict) and isinstance(bounds, dict):
                        for class_type in user_param.keys():
                            if class_type not in bounds:
                                raise ValueError(
                                    f"Initial value class {class_type!r} for parameter "
                                    f"{name!r} in group {param_group!r}, unit {unit} "
                                    f"not found in parameter_bounds."
                                )

        initial_values = {}
        for param_group, param_dict in bounds_dict.items():
            initial_values[param_group] = {}
            user_group = initial_values_dict.get(param_group, {})
            if not param_dict or not isinstance(param_dict, dict):
                continue
            for unit, params in param_dict.items():
                initial_values[param_group][unit] = {}
                user_unit = user_group.get(unit, {}) if isinstance(user_group, dict) else {}
                if not isinstance(params, dict):
                    continue
                for name, bounds in params.items():
                    user_param = user_unit.get(name) if isinstance(user_unit, dict) else None
                    if isinstance(bounds, dict):
                        # Per-class bounds
                        initial_values[param_group][unit][name] = {}
                        if isinstance(user_param, dict):
                            for class_type, class_bounds in bounds.items():
                                if class_type in user_param:
                                    try:
                                        initial_values[param_group][unit][name][class_type] = \
                                            self._format_initial_value(user_param[class_type], class_bounds)
                                    except (ValueError, TypeError) as exc:
                                        raise type(exc)(
                                            f"Parameter {name!r} (group {param_group!r}, "
                                            f"unit {unit}, class {class_type!r}): {exc}"
                                        ) from exc
                                else:
                                    initial_values[param_group][unit][name][class_type] = \
                                        self._draw_initial(class_bounds)
                        elif user_param is not None:
                            raise TypeError(
                                f"Parameter {name!r} (group {param_group!r}, unit {unit}): "
                                f"initial value is a single value, but bounds are per-class. "
                                f"Provide a dict of per-class values."
                            )
                        else:
                            for class_type, class_bounds in bounds.items():
                                initial_values[param_group][unit][name][class_type] = \
                                    self._draw_initial(class_bounds)
                    elif isinstance(bounds, (list, tuple)) and len(bounds) >= 2:
                        if isinstance(user_param, dict):
                            raise TypeError(
                                f"Parameter {name!r} (group {param_group!r}, unit {unit}): "
                                f"initial value is a dict (per-class), but bounds are not per-class. "
                                f"Provide a single numeric value."
                            )
                        if user_param is not None:
                            try:
                                initial_values[param_group][unit][name] = \
                                    self._format_initial_value(user_param, bounds)
                            except (ValueError, TypeError) as exc:
                                raise type(exc)(
                                    f"Parameter {name!r} (group {param_group!r}, "
                                    f"unit {unit}): {exc}"
                                ) from exc
                        else:
                            initial_values[param_group][unit][name] = self._draw_initial(bounds)
        return initial_values

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
        info_dict['initial_values'] = self._generate_initial_values(
            self.model.parameter_bounds,
            seed=info_dict.get('random_seed'),
            initial_values_dict=getattr(self.model, 'parameter_initial_values', None),
        )

        # Sanitize callable keys in objective_functions so that Jinja2
        # renders clean names (e.g., ``pbias``) instead of ``repr(function)``.
        if 'objective_functions' in info_dict and isinstance(
            info_dict['objective_functions'], dict
        ):
            sanitized = {}
            for group, fluxes in info_dict['objective_functions'].items():
                sanitized[group] = {}
                for flux, metrics in fluxes.items():
                    sanitized[group][flux] = {
                        (k.__name__ if callable(k) else k): v
                        for k, v in metrics.items()
                    }
            info_dict['objective_functions'] = sanitized
        
        # Sanitize callable keys in constraints (same pattern as objective_functions)
        if 'constraints' in info_dict and isinstance(
            info_dict['constraints'], dict
        ):
            sanitized_constraints = {}
            for group, fluxes in info_dict['constraints'].items():
                sanitized_constraints[group] = {}
                for flux, metrics in fluxes.items():
                    sanitized_constraints[group][flux] = {
                        (k.__name__ if callable(k) else k): v
                        for k, v in metrics.items()
                    }
            info_dict['constraints'] = sanitized_constraints

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
            model=self.model.model_software.lower(),
            timestamp=datetime.now().strftime('%Y-%m-%dT%H.%M.%S'),
            calibration_root=os.path.abspath(output_path),
        )
        with open(archive_script_path, 'w') as f:
            f.write(archive_content)

        # make sure the script is executable
        os.chmod(archive_script_path, 0o755)

        # if `others` attribute is populated (not an empty dictionary)
        if len(self.model.others) > 0:
            for group in self.model.others.keys():
                dtype = self.model.others[group]['type']
                params = self.model.others[group]['data']
                if dtype == 'json': 
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
                elif dtype == 'nc':
                    params.to_netcdf(
                        os.path.join(
                            output_path,
                            'etc',
                            'templates',
                            f'{group}.nc',
                        )
                    )
                else:
                    raise ValueError(
                        f"Unsupported data type `{dtype}` for `others` group "
                        f"`{group}`. Supported types are `json` and `netcdf`."
                    )

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
        # go over the optional files, and if they exist, copy them as well
        for file in self.model.optional_files:
            src_path = os.path.join(self.model.config['instance_path'], file)
            if os.path.exists(src_path):
                shutil.copy(
                    src_path,
                    model_output_path,
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