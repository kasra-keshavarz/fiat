"""FIATModel main entry point"""

# 3rd-party imports
import sys
import pandas as pd

# build-in imports
import re
import json
import sys
import os

from typing import (
    Dict,
    List,
    Sequence,
    Union,
)
from pathlib import Path
from io import StringIO

# defining custom types
if sys.version_info >= (3, 10):
    from typing import TypeAlias
    PathLike: TypeAlias = Union[str, Path]
else:
    PathLike = Union[str, Path]

class Calibration(object):
    """
    """

    def __init__(
        self,
        calibration_software: str = 'ostrich',
        model_software: str = 'mesh',
        calibration_config: Dict = None,
        model_config: Dict = None,
    ) -> None:
        """
        Initialize the Calibration class.

        Parameters
        ----------
        calibration_software : str
            The software used for calibration. Default is 'ostrich'.
        model_software : str
            The software used for the model. Default is 'mesh'.
        calibration_config : Dict
            Configuration parameters for the calibration software.
        model_config : Dict
            Configuration parameters for the model software.

        Returns
        -------
        None
        """
        # check data types
        if not isinstance(calibration_software, str):
            raise TypeError('`calibration_software` must be a string')
        if not isinstance(model_software, str):
            raise TypeError('`model_software` must be a string')
        if calibration_config is not None and not isinstance(calibration_config, dict):
            raise TypeError('`calibration_config` must be a dictionary')
        if model_config is not None and not isinstance(model_config, dict):
            raise TypeError('`model_config` must be a dictionary')

        # assign object attributes
        self.calibration_software = calibration_software
        self.model_software = model_software
        self.calibration_config = calibration_config
        self.model_config = model_config

        return

    @classmethod
    def from_json(cls, json_path: str):
        with open(json_path, 'r') as f:
            data = json.load(f)
        return cls(**data)

    @classmethod
    def from_dict(cls, data: dict):
        return cls(**data)

    def __repr__(self):
        return 'Calibration()'

    def __str__(self):
        return 'Calibration()'
    
    def build(self, save_path: PathLike = None):
        """Build the calibration workflow."""
        return

    def evaluate(self):
        """Evaluate the model performance."""
        return
    
    def observations(self):
        """Load and process observational data."""
        return

    def to_dict(self) -> dict:
        """Convert the object to a dictionary."""
        return self.__dict__

    def to_json(self) -> str:
        """Serialize the object to a JSON string."""
        return json.dumps(self.__dict__)

