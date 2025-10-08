"""General builder class for the package."""
from typing import Dict

class Builder(object):
    """Base class for all builders in the package."""
    def __init__(self, config: Dict) -> None:
        self.config = config
        
        # some of the following 
        # assign an empty list for the `forcing_file` attribute 
        self.forcing_file = []
        # similarly, for the `required_files` attribute
        self.required_files = []

        return

    def build(self) -> None:
        """Build the model."""
        raise NotImplementedError("Subclasses must implement this method.")
    
    def sanity_check(self) -> bool:
        """Perform sanity checks on the configured instance."""
        raise NotImplementedError("Subclasses must implement this method.")

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(config={self.config})"
    