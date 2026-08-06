"""File permission management for generated calibration instances.

Provides helpers to set consistent file and directory permissions on
the artifacts generated during the initialization of a calibration
instance, based on the defaults in :file:`defaults.json`.
"""
# built-in imports
import sys
import os

from typing import (
    Union,
    Sequence,
)
from pathlib import Path

# internal imports
from .defaults import load_defaults

# defining custom types
if sys.version_info >= (3, 10):
    from typing import TypeAlias
    PathLike: TypeAlias = Union[str, Path]
else:
    PathLike = Union[str, Path]

def set_default_permissions(
    path: PathLike,
    executable_names: Sequence[str] = (),
) -> None:
    """Set default file permissions across a directory tree.

    Walks the directory rooted at ``path`` and sets file and directory
    permissions based on the defaults in :file:`defaults.json`.
    Directories receive ``permissions['directory']``. Files whose
    basename is listed in ``executable_names`` or whose extension is
    included in ``executable_extensions`` receive
    ``permissions['executable']``; all other files receive
    ``permissions['regular_file']``.

    Parameters
    ----------
    path : PathLike
        Root directory of the tree to update.
    executable_names : sequence of str, optional
        Basenames of files that must be treated as executable
        regardless of their extension (e.g., the model executable).

    Notes
    -----
    - Symlinks are skipped to avoid modifying their targets.
    - Extension and name matching are case-insensitive.
    """
    defaults = load_defaults()
    permissions = defaults['permissions']
    executable_extensions = defaults['executable_extensions']
    lower_executable_names = [
        os.path.basename(name).lower() for name in executable_names
    ]

    for root, dirs, files_ in os.walk(path):
        # set directory permissions
        for dir_name in dirs:
            dir_path = os.path.join(root, dir_name)
            if not os.path.islink(dir_path):
                os.chmod(dir_path, permissions['directory'])

        # set file permissions
        for file_name in files_:
            file_path = os.path.join(root, file_name)
            if os.path.islink(file_path):
                continue
            _, ext = os.path.splitext(file_name)
            is_executable = (
                file_name.lower() in lower_executable_names
                or ext.lower() in executable_extensions
            )
            mode = (
                permissions['executable']
                if is_executable else permissions['regular_file']
            )
            os.chmod(file_path, mode)

    # normalize the root directory itself
    if not os.path.islink(path):
        os.chmod(path, permissions['directory'])

    return
