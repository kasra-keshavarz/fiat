"""Utility functions for Fiat Model.

Aggregates the public API of the package-wide utility modules, e.g.,
:func:`union_sorted_times` for datetime handling and the file
permission helpers applied to calibration instances.

Attributes
----------
PathLike : type alias
    Alias for ``str`` or :class:`pathlib.Path`.
"""
# built-in imports
import sys

from typing import (
    Union,
)
from pathlib import Path

# internal imports
from .datetime_utils import union_sorted_times
from .defaults import load_defaults
from .file_permissions import set_default_permissions

# defining custom types
if sys.version_info >= (3, 10):
    from typing import TypeAlias
    PathLike: TypeAlias = Union[str, Path]
else:
    PathLike = Union[str, Path]
