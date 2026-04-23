"""Unit test: runtime __version__ must match pyproject.toml."""

import tomllib
from pathlib import Path

from softarr.version import __version__

PYPROJECT = Path(__file__).resolve().parents[2] / "pyproject.toml"


class TestAppVersion:
    def test_version_matches_pyproject(self):
        """`softarr.version.__version__` must equal `project.version` in pyproject.toml."""
        with PYPROJECT.open("rb") as f:
            data = tomllib.load(f)
        assert __version__ == data["project"]["version"]

    def test_version_is_semver(self):
        """Version must follow major.minor.patch format."""
        parts = __version__.split(".")
        assert len(parts) == 3
        for part in parts:
            assert part.isdigit(), f"Non-numeric version part: {part!r}"
