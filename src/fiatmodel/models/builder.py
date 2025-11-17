"""General builder infrastructure for model calibration.

Defines the abstract :class:`ModelBuilder` base used by concrete model
implementations to analyze configurations, prepare run assets, and
materialize calibration-ready instances.
"""
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
    """Base class for model builders used in FIAT.

    Orchestrates common state and lifecycle for model-specific builders.

    Parameters
    ----------
    config : dict
        Configuration dictionary for the model instance (paths, options).
    calibration_software : str
        Name of the calibration engine (e.g., ``"ostrich"``).
    model_software : str
        Name of the model targeted by this builder (e.g., ``"mesh"``).
    fluxes : Sequence[str], optional
        Model flux variables of interest to be produced or analyzed.
    dates : dict[str, Sequence[str]] or None, optional
        Calibration start/stop window and other date ranges. When omitted,
        a warning is issued and defaults are assumed elsewhere.

    Attributes
    ----------
    config : dict
        Stored configuration for the model instance.
    calibration_software : str
        Selected calibration engine name.
    model_software : str
        Model software family in lower case.
    forcing_file : list
        Paths to forcing files referenced by the model (may be empty).
    forcing_freq : object or None
        Frequency associated with forcing inputs when available.
    required_files : list[str]
        Files that must be staged into the model run directory.
    required_dirs : list[str]
        Directories that must be staged into the model run directory.
    step_logger : dict[str, bool]
        Flags indicating whether lifecycle steps ran (``analyze``, ``prepare``, ``build``).
    parameters : dict
        Parameter definitions discovered during analysis.
    templated_parameters : dict
        Parameter groups ready to be written as templates.
    parameter_constraints : dict
        Optional constraints applied to parameters by the calibration.
    parameter_bounds : dict
        Optional bounds applied to parameters by the calibration.
    others : dict
        Additional model-specific objects to template (e.g., options files).
    fluxes : Sequence[str]
        Flux variables to output or evaluate.
    outputs : list
        Output files produced by the model (populated by subclasses).
    dates : dict[str, Sequence[str]]
        Date window(s) used for calibration and evaluation.

    Methods
    -------
    analyze()
        Inspect configuration and populate parameter structures.
    prepare()
        Stage files/directories needed to build a calibration instance.
    build(save_path)
        Create a calibration-ready instance under ``save_path``.
    sanity_check()
        Validate internal state; implemented by subclasses.
    """
    def __init__(
        self,
        config: Dict,
        calibration_software: str,
        model_software: str,
        fluxes: Sequence[str] = [],
        dates: Dict[str, Sequence[str]] = None,
    ) -> None:
        """Initialize common builder state.

        Parameters
        ----------
        config : dict
            Model configuration dictionary used by the builder.
        calibration_software : str
            Calibration engine name.
        model_software : str
            Model software name.
        fluxes : Sequence[str], optional
            Flux variables to track for outputs, by default empty.
        dates : dict[str, Sequence[str]] or None, optional
            Calibration period definitions; when omitted, a warning is issued.
        """
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
        self.outputs = []  # to be populated later by child classes

        # dates must be provided, otherwise, warn the user
        if dates is None:
            warnings.warn(
                "`dates` not provided. Calibration iterations will not be accurate.",
                UserWarning
            )
        else:
            if not isinstance(dates, Sequence):
                raise TypeError('`dates` must be a dictionary')
            self.dates = dates

        return

    def build(
        self,
        save_path: PathLike = None) -> None:
        """Build a calibration-ready model instance at the target path.

        Uses the current configuration and analyzed parameters to assemble
        a runnable directory structure for the chosen calibration workflow.

        Parameters
        ----------
        save_path : PathLike
            Destination directory for the built instance (``str`` or
            :class:`pathlib.Path`).
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
        """Perform sanity checks on the configured instance.

        Returns
        -------
        bool
            True if checks pass; subclasses should raise errors otherwise.
        """
        raise NotImplementedError("Subclasses must implement this method.")

    def __repr__(self) -> str:
        """Representation for debugging.

        Returns
        -------
        str
            A concise string with the class name and key state.
        """
        return f"{self.__class__.__name__}(config={self.config})"
    
    def analyze(self) -> None:
        """Analyze configuration and populate parameter structures.

        Subclasses should read model metadata and construct ``parameters``,
        ``templated_parameters``, ``parameter_bounds``, and related state.
        """
        raise NotImplementedError("Subclasses must implement this method.")
    
    def prepare(self) -> None:
        """Prepare files and directories for building the instance.

        Subclasses should gather and stage all prerequisite assets required
        to execute :meth:`build`.
        """
        raise NotImplementedError("Subclasses must implement this method.")
    
