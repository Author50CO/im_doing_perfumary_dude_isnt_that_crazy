from __future__ import annotations

import faulthandler
import logging
import sys
from pathlib import Path

_log_handle = None


def install_crash_logging(data_dir: Path):
    """Install persistent Python/fatal crash logging next to the user's database."""
    global _log_handle
    data_dir.mkdir(parents=True, exist_ok=True)
    log_path = data_dir / "PerfumeStudio.log"
    logging.basicConfig(
        filename=log_path,
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        force=True,
    )
    logging.info("Perfume Studio starting")
    try:
        _log_handle = open(log_path, "a", encoding="utf-8", buffering=1)
        faulthandler.enable(_log_handle, all_threads=True)
    except Exception:
        logging.exception("Could not enable faulthandler")

    previous = sys.excepthook

    def _hook(exc_type, exc_value, exc_tb):
        logging.critical("Unhandled exception", exc_info=(exc_type, exc_value, exc_tb))
        try:
            previous(exc_type, exc_value, exc_tb)
        except Exception:
            pass

    sys.excepthook = _hook
    return log_path
