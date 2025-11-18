"""Core calibration templating utilities.

Provides the base :class:`OptimizerTemplateEngine` used to render calibration
configuration, parameter, model, observation, and auxiliary (``etc``) files
for different optimization backends.

Notes
-----
The concrete subclasses (e.g., Ostrich templating engines) are expected to
implement the abstract generation methods in this module. This file only
declares the shared interface and infrastructure (Jinja2 environment,
path handling, and validation helpers).
"""
# built-in imports
import sys
import os
import warnings

from importlib.resources import files

from typing import (
    Dict,
    Union,
)
from pathlib import Path

# 3rd party imports
import jinja2

# internal imports
from . import (
    available_calibration_software,
    available_model_software,
)

# defining custom types
if sys.version_info >= (3, 10):
    from typing import TypeAlias
    PathLike: TypeAlias = Union[str, Path]
else:
    PathLike = Union[str, Path]

# global variables and helper functions
def raise_helper(msg: str) -> None:
    """Raise an exception inside Jinja2 templates.

    Parameters
    ----------
    msg : str
        Message to include in the raised :class:`Exception`.

    Raises
    ------
    Exception
        Always raised with the provided message.
    """
    raise Exception(msg)

class OptimizerTemplateEngine(object):
    """Base templating engine for calibration workflows.

    Sets up a :class:`jinja2.Environment` pointing to the calibration software
    template directory and exposes methods that subclasses must implement to
    materialize optimizer, parameter, model, observation, and auxiliary files.

    Parameters
    ----------
    config : dict
        Calibration configuration dictionary consumed by templates.
    calibration_software : str
        Name of the calibration engine (must exist in
        ``available_calibration_software``).
    model : ModelBuilder
        Model adapter instance providing parameters, constraints, and metadata.

    Attributes
    ----------
    environment : :class:`jinja2.Environment`
        Jinja2 environment with filesystem loader for the software template path.
    calibration_software : str
        Normalized calibration software name (lower-case).
    model : ModelBuilder
        The model adapter associated with this calibration.
    config : dict
        Stored calibration configuration dictionary.

    Methods
    -------
    generate_optimizer_templates(output_path)
        Generate optimizer configuration artifacts (abstract; implemented by subclasses).
    generate_parameter_templates(output_path)
        Generate parameter specification artifacts (abstract).
    generate_model_templates(output_path)
        Generate model-related template artifacts (abstract).
    generate_etc_templates(output_path)
        Generate auxiliary (``etc``) artifacts (abstract).
    generate_obs_templates(output_path)
        Generate observation artifacts (abstract).
    sanity_checks()
        Validate that required model attributes (parameters, constraints) are present.
    _create_dir(path)
        Internal helper to create a directory (warns if exists).
    """

    def __init__(
        self,
        config: Dict,
        calibration_software: str,
        model: 'ModelBuilder'  # type: ignore
    ) -> 'OptimizerTemplateEngine':
        """Construct the base templating engine.

        Parameters
        ----------
        config : dict
            Calibration configuration dictionary.
        calibration_software : str
            Name of the calibration engine to use.
        model : ModelBuilder
            Model adapter instance supplying parameter metadata and constraints.

        Returns
        -------
        OptimizerTemplateEngine
            The initialized instance (standard Python convention returns None; type shown for clarity).

        Raises
        ------
        TypeError
            If ``calibration_software`` is not a string.
        ValueError
            If calibration or model software names are unsupported.
        """
        package_path = os.path.join(
            files(__package__),
            calibration_software,
            "templates")

        # Jinja2 global environment setup placeholder
        self.environment = jinja2.Environment(
            # loader=PackageLoader("meshflow", "templates"),
            loader=jinja2.FileSystemLoader(package_path),
            trim_blocks=True,
            lstrip_blocks=True,
            line_comment_prefix='##',
        )
        # referring to the global raise helper function
        self.environment.globals['raise'] = raise_helper

        # check the `calibration_software` and `model` types and values
        if not isinstance(calibration_software, str):
            raise TypeError('`calibration_software` must be a string')
        if calibration_software.lower() not in available_calibration_software:
            raise ValueError(
                f"`calibration_software` '{calibration_software}' is not supported."
            )
        self.calibration_software = calibration_software 

        # check the `model` type
        if model.model_software.lower() not in available_model_software.get(self.calibration_software):
            raise ValueError(
                f"`model` software '{model.model_software}' does not match "
                f"available recipes for {self.calibration_software}."
            )
        self.model = model

        # assign all other necessary attributes
        self.config = config

        return

    def generate_optimizer_templates(
        self,
        output_path: PathLike,
    ) -> None:
        """Generate optimizer configuration artifacts.

        Parameters
        ----------
        output_path : PathLike
            Directory where optimizer configuration files should be written.

        Raises
        ------
        NotImplementedError
            Always; must be implemented by subclasses.
        """
        raise NotImplementedError("This method should be implemented by subclasses.")

    def generate_parameter_templates(
        self,
        output_path: PathLike,
    ) -> None:
        """Generate parameter specification artifacts.

        Parameters
        ----------
        output_path : PathLike
            Directory where parameter files should be written.

        Raises
        ------
        NotImplementedError
            Always; must be implemented by subclasses.
        """
        raise NotImplementedError("This method should be implemented by subclasses.")

    def generate_model_templates(
        self,
        output_path: PathLike,
    ) -> None:
        """Generate model-related template artifacts.

        Parameters
        ----------
        output_path : PathLike
            Directory where model template files should be written.

        Raises
        ------
        NotImplementedError
            Always; must be implemented by subclasses.
        """
        raise NotImplementedError("This method should be implemented by subclasses.")

    def sanity_checks(self) -> None:
        """Validate presence of needed model attributes.

        Checks that model parameters, templated parameters, and (optionally)
        parameter constraints are populated; emits warnings for missing
        non-critical pieces.

        Raises
        ------
        ValueError
            If required model metadata (e.g., parameters) is missing.
        UserWarning
            If constraints are absent (warning issued via :func:`warnings.warn`).
        """

        # check whether the important attributes of the `model` object are set
        if self.model.parameters is None or len(self.model.parameters) == 0:
            raise ValueError("The `model` object must have its `parameters`"
                             " attribute populated. Try initializing the model"
                             " first.")
        if self.model.templated_parameters is None or len(self.model.templated_parameters) == 0:
            raise ValueError("The `model` object must have its `templated_parameters`"
                             " attribute populated. Try initializing the model"
                             " first.")
        if self.model.parameter_constraints is None or len(self.model.parameter_constraints) == 0:
            warnings.warn("No parameter constraints is set for"
                          " the model calibration.", UserWarning)

        return

    def generate_etc_templates(
        self,
        output_path: PathLike,
    ) -> None:
        """Generate auxiliary (``etc``) template artifacts.
        This encompasses anything not covered by other generation
        methods.

        Parameters
        ----------
        output_path : PathLike
            Directory where auxiliary files should be written.

        Raises
        ------
        NotImplementedError
            Always; must be implemented by subclasses.
        """
        raise NotImplementedError("This method should be implemented by subclasses.")

    def generate_obs_templates(
        self,
        output_path: PathLike,
    ) -> None:
        """Generate observation artifacts required by the calibration backend.

        Parameters
        ----------
        output_path : PathLike
            Directory where observation files should be written.

        Raises
        ------
        NotImplementedError
            Always; must be implemented by subclasses.
        """
        raise NotImplementedError("This method should be implemented by subclasses.")

    def _create_dir(self, path: PathLike) -> None:
        """Create a directory, warning if it already exists.

        Parameters
        ----------
        path : PathLike
            Target directory path to create.

        Notes
        -----
        Uses :func:`os.makedirs` with ``exist_ok=True`` and issues a
        :class:`UserWarning` if the path already exists.
        """
        # if `path` exists, give a warning and create the path nonetheless
        if os.path.exists(path):
            warnings.warn(f"The directory {path} already exists."
                          " Contents may be overwritten.", UserWarning)
        # create the directory
        os.makedirs(path, exist_ok=True)

        return
