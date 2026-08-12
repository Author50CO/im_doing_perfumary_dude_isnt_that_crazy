from __future__ import annotations
import sys
from pathlib import Path
from PySide6.QtWidgets import QApplication
from perfume_studio.database import Database
from perfume_studio.ui.main_window import MainWindow
from perfume_studio.services.bundled_reference import ensure_bundled_reference_data, enrich_existing_inventory_db, resource_data_dir
from perfume_studio.services.formula_io import normalize_all_formulas_to_1000
from perfume_studio.logging_utils import install_crash_logging


def runtime_dir() -> Path:
    if getattr(sys,'frozen',False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def main():
    root=runtime_dir(); data=root/'user_data'; data.mkdir(exist_ok=True)
    install_crash_logging(data)
    db=Database(data/'perfume_studio.db')
    # Install the bundled IFRA 51 data and offline fragrance identity aliases before UI startup.
    # This performs no network requests and is idempotent across launches.
    ensure_bundled_reference_data(db, resource_data_dir())
    enrich_existing_inventory_db(db)
    normalize_all_formulas_to_1000(db)
    app=QApplication(sys.argv); app.setApplicationName('Perfume Studio')
    win=MainWindow(db,data); win.show(); sys.exit(app.exec())

if __name__=='__main__': main()
