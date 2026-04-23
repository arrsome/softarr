"""Application version, read from pyproject.toml via installed package metadata.

All places that display or return a version string should import from here.
The single source of truth is the ``version`` field in pyproject.toml.
"""

import importlib.metadata

__version__ = importlib.metadata.version("softarr")
