"""
Global Optimizer class to specify necessary attributes
and methods for a successful calibration optimization."""
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
def raise_helper(msg):
    """Jinja2 helper function to raise exceptions."""
    raise Exception(msg)

class OptimizerTemplateEngine(object):
    """
    A base templating engine for generating configuration files
    for different calibration software.
    """

    def __init__(
        self,
        config: Dict,
        calibration_software: str,
        model: 'ModelBuilder' # type: ignore
    ) -> 'OptimizerTemplateEngine':
        """
        Initialize the OptimizerTemplateEngine class.
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
        """
        Generate a configuration file based on the provided parameters
        and a template file.

        Parameters
        ----------
        output_path : PathLike
            The path where the generated configuration file will be saved.

        Returns
        -------
        None
        """
        raise NotImplementedError("This method should be implemented by subclasses.")

    def generate_parameter_templates(
        self,
        output_path: PathLike,
    ) -> None:
        """
        Generate a parameter file based on the provided parameters
        and a template file.

        Parameters
        ----------
        output_path : PathLike
            The path where the generated parameter file will be saved.

        Returns
        -------
        None
        """
        raise NotImplementedError("This method should be implemented by subclasses.")

    def generate_model_templates(
        self,
        output_path: PathLike,
    ) -> None:
        """
        Generate a model file based on the provided parameters
        and a template file.

        Parameters
        ----------
        output_path : PathLike
            The path where the generated model file will be saved.

        Returns
        -------
        None
        """
        raise NotImplementedError("This method should be implemented by subclasses.")

    def sanity_checks(self) -> None:
        """
        Perform sanity checks on the configuration.

        Returns
        -------
        None
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
        """
        Generate the `etc` directory to store auxiliary files.

        Parameters
        ----------
        output_path : PathLike
            The path where the `etc` directory will be created.

        Returns
        -------
        None
        """
        raise NotImplementedError("This method should be implemented by subclasses.")

    def _create_dir(self, path: PathLike) -> None:
        """Create directory if it does not exist."""
        # if `path` exists, give a warning and create the path nonetheless
        if os.path.exists(path):
            warnings.warn(f"The directory {path} already exists."
                          " Contents may be overwritten.", UserWarning)
        # create the directory
        os.makedirs(path, exist_ok=True)

        return
