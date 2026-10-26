"""Where Clawdlet keeps per-user data and finds its bundled assets (source tree or PyInstaller)."""

from __future__ import annotations

import os
from pathlib import Path
import sys

APP_DIR_NAME = "Clawdlet"
USER_BEHAVIORS_FILE = "behaviors.json"


def app_data_dir() -> Path:
    """Per-user data folder: ``%APPDATA%\\Clawdlet`` on Windows, ``~/Clawdlet`` elsewhere."""
    base = os.environ.get("APPDATA") or str(Path.home())
    return Path(base) / APP_DIR_NAME


def assets_dir() -> Path:
    """The ``assets`` folder in a source checkout / editable install or a PyInstaller bundle."""
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass is not None:
        return Path(str(meipass)) / "assets"
    return Path(__file__).resolve().parents[2] / "assets"


def default_behaviors_path() -> Path:
    """The bundled behavior table (always present, never edited by the user)."""
    return assets_dir() / "behaviors" / "behaviors.default.json"


def behaviors_schema_path() -> Path:
    return assets_dir() / "behaviors" / "behaviors.schema.json"


def user_behaviors_path() -> Path:
    """The user's optional override file, merged over the bundled table."""
    return app_data_dir() / USER_BEHAVIORS_FILE
