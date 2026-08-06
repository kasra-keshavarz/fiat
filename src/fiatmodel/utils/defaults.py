"""Access to the package-wide default configurations.

Provides loaders for the static defaults stored alongside this package
(e.g., :file:`defaults.json`).
"""
# built-in imports
import json

from importlib.resources import files

from typing import (
    Any,
    Dict,
)

def load_defaults() -> Dict[str, Any]:
    """Load and parse the package-wide default configuration.

    Returns
    -------
    dict
        Contents of :file:`defaults.json` with the permission mode
        strings (e.g., ``"0o775"``) parsed to octal integers.
    """
    with files(__package__).joinpath('defaults.json').open('r') as f:
        defaults = json.load(f)

    # parse the octal mode strings into integers
    for key, value in defaults.get('permissions', {}).items():
        defaults['permissions'][key] = int(value, 8)

    return defaults
