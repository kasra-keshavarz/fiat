"""General builder class for the package."""
# builtin imports
from typing import (
    Dict,
    Sequence,
    Union,
)
from pathlib import Path

import sys
import warnings

# internal imports
from ..calibration import available_calibration_software

# custom types
# PathLike type alias for file system paths
if sys.version_info >= (3, 10):
    from typing import TypeAlias
    PathLike: TypeAlias = Union[str, Path]
else:
    PathLike = Union[str, Path]
# NameType type alias for parameter names
NameType = Union[str, int, float]

class ModelBuilder(object):
    """Base class for all builders in the package."""
    def __init__(
        self,
        config: Dict,
        calibration_software: str,
        model_software: str,
        fluxes: Sequence[str] = [],
    ) -> None:
         # store the configuration dictionary
        if not isinstance(config, dict):
            raise TypeError('`config` must be a dictionary')
        self.config = config

        # store the calibration software name
        if not isinstance(calibration_software, str):
            raise TypeError('`calibration_software` must be a string')
        self.calibration_software = calibration_software

        # check whether a valid calibration software is provided
        if self.calibration_software.lower() not in available_calibration_software:
            raise ValueError(
                f"Unsupported calibration software: {self.calibration_software}. "
                f"Available options are: {available_calibration_software}"
            )

        # some of the following 
        # assign an empty list for the `forcing_file` attribute 
        self.forcing_file = []
        # initialize the `forcing_freq` attribute to None
        self.forcing_freq = None
        # similarly, for the `required_files` and `required_dirs` attributes
        self.required_files = []
        self.required_dirs = []
        # logging various steps
        self.step_logger = {
            'analyze': False,
            'prepare': False,
            'build': False,
        }

        # necessary attributes to be populated later by child classes
        self.parameters = {}
        self.others = {}
        self.templated_parameters = {}
        self.parameter_constraints = {} # to be overridden by child classes
        self.parameter_bounds = {} # to be overridden by child classes
        self.model_software = model_software.lower()

        # fluxes for outputing
        self.fluxes = fluxes

        return

    def build(
        self,
        save_path: PathLike = None) -> None:
        """Build a calibration instance of MESH model given
        the available `self.config` dictionary and
        the analyzed `self.parameters` dictionary.

        Parameters
        ----------
        save_path : PathLike
            The path where the built instance will be saved.
        """
        # check whether `save_path` is provided
        if save_path is None:
            raise ValueError("A valid `save_path` must be provided.")
        if not isinstance(save_path, (str, Path)):
            raise TypeError("`save_path` must be a string or a Path object.")

        # convert to Path object (if it's a string)
        save_path = Path(save_path)

        # check whether `save_path` exists, if not, create it
        if save_path.exists():
            warnings.warn(
                f"The provided `save_path` {save_path} already exists. "
                f"Continuing and using the existing directory.",
                UserWarning
            )
        # create the directory regardless of its existence
        save_path.mkdir(parents=True, exist_ok=True)

        # depending on the `calibration_software`, trigger the proper 
        # calibration instantiation build module

        return

    def sanity_check(self) -> bool:
        """Perform sanity checks on the configured instance."""
        raise NotImplementedError("Subclasses must implement this method.")

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(config={self.config})"
    
    def analyze(self) -> None:
        """Analyze the provided configuration and populate
        the `self.parameters` dictionary."""
        raise NotImplementedError("Subclasses must implement this method.")
    
    def prepare(self) -> None:
        """Prepare the necessary files and directories
        for building the calibration instance."""
        raise NotImplementedError("Subclasses must implement this method.")
    
