import os
import sys


def safe_float(value, fallback=None):
    try:
        if value is None or str(value).strip() == "":
            return fallback
        return float(value)
    except (ValueError, TypeError):
        return fallback


def resource_path(relative_path: str) -> str:
    """Works in normal Python and PyInstaller bundled apps."""
    try:
        base_path = sys._MEIPASS  # type: ignore[attr-defined]
    except AttributeError:
        base_path = os.path.abspath(".")
    return os.path.join(base_path, relative_path)
