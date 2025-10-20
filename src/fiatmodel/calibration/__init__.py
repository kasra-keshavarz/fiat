"""
Including a list of available calibration modules.
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

