"""Calibration backends and compatibility matrix.

Exposes the set of available calibration engines and the compatible
model software for each engine. This module also re-exports the
Ostrich templating engine for convenience.

Attributes
----------
available_calibration_software : list of str
        Supported calibration engines available in this package.
available_model_software : dict[str, list[str]]
        Mapping from calibration engine name to a list of compatible model names.

Notes
-----
- The Ostrich templating engine is re-exported as
    :class:`fiatmodel.calibration.ostrich.templating.OstrichTemplateEngine`.
"""

available_calibration_software = [
    'ostrich',
]

available_model_software = {
    'ostrich': ['mesh'],
}

# OSTRICH's templating engine
# These imports can be streamlined in future versions
from .ostrich.templating import OstrichTemplateEngine

