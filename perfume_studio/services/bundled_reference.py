from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

from perfume_studio.services.ifra_importer import import_ifra_overview, import_ncs_annex

IDENTITY_VERSION = '2026-08-12-v5-muscenone-cas-search'
IFRA_BUNDLE_VERSION = '51-2026-08-bundle1'


def resource_data_dir() -> Path:
    """Return the read-only data directory in source and PyInstaller builds."""
    if getattr(sys, 'frozen', False):
        root = Path(getattr(sys, '_MEIPASS', Path(sys.executable).resolve().parent))
        return root / 'data'
    return Path(__file__).resolve().parents[2] / 'data'


def _get_meta(db, key: str) -> str | None:
    rows = db.query('SELECT value FROM app_meta WHERE key=?', (key,))
    return rows[0]['value'] if rows else None


def _set_meta(db, key: str, value: str) -> None:
    db.execute('''INSERT INTO app_meta(key,value) VALUES(?,?)
                  ON CONFLICT(key) DO UPDATE SET value=excluded.value''', (key, value))


def install_identity_reference(db, data_dir: Path | None = None) -> dict:
    data_dir = Path(data_dir or resource_data_dir())
    ref_path = data_dir / 'fragrance_identity_reference.sqlite'
    if not ref_path.exists():
        return {'installed': 0, 'available': 0, 'path': str(ref_path), 'error': 'reference database missing'}

    # Read the bundled reference DB separately. It is deliberately read-only at runtime.
    ref = sqlite3.connect(ref_path)
    ref.row_factory = sqlite3.Row
    try:
        rows = ref.execute('''SELECT alias, normalized_alias, principal_name, cas, source, confidence, notes
                              FROM identity_aliases''').fetchall()
        candidate_rows = []  # v0.8 resolver does not use material-specific hardcoded candidate groups.
    finally:
        ref.close()

    installed = 0
    installed_candidates = 0
    with db.connect() as conn:
        for row in rows:
            # Never overwrite a user-confirmed alias. Seed upgrades only fill names that
            # do not exist in the user's database yet.
            cur = conn.execute(
                '''INSERT OR IGNORE INTO material_aliases(
                       alias, normalized_alias, principal_name, cas, source, confidence, notes
                   ) VALUES(?,?,?,?,?,?,?)''',
                (row['alias'], row['normalized_alias'], row['principal_name'], row['cas'],
                 'Bundled: ' + row['source'], float(row['confidence'] or 1.0), row['notes'] or '')
            )
            installed += max(0, cur.rowcount)
        # Remove old bundled query-specific candidate answers from user DBs.
        conn.execute("DELETE FROM material_resolution_candidates WHERE source LIKE 'Bundled:%'")
        conn.execute('''INSERT INTO app_meta(key,value) VALUES('identity_reference_version',?)
                        ON CONFLICT(key) DO UPDATE SET value=excluded.value''', (IDENTITY_VERSION,))
    return {
        'installed': installed, 'available': len(rows),
        'candidate_installed': installed_candidates, 'candidate_available': len(candidate_rows),
        'path': str(ref_path)
    }


def ensure_bundled_ifra51(db, data_dir: Path | None = None) -> dict:
    """Ensure official IFRA 51 overview + NCS annex are available without any network call."""
    data_dir = Path(data_dir or resource_data_dir())
    overview = data_dir / 'ifra-51st-amendment-ifra-standards-overview.xlsx'
    annex = data_dir / 'ifra-51st-amendment-annex-on-contributions-from-other-sources.xlsx'
    nstd = int(db.query('SELECT COUNT(*) n FROM ifra_standards')[0]['n'])
    nncs = int(db.query('SELECT COUNT(*) n FROM ncs_contributions')[0]['n'])

    result = {'standards': nstd, 'ncs_contributions': nncs, 'imported': False}
    # Existing user DBs may already have the same official data. Do not destructively
    # re-import merely because an app_meta marker did not exist in older versions.
    if nstd == 0:
        if not overview.exists():
            raise FileNotFoundError(f'Bundled IFRA overview missing: {overview}')
        a = import_ifra_overview(db, overview)
        result['standards'] = a['standards']
        result['imported'] = True
    if nncs == 0:
        if not annex.exists():
            raise FileNotFoundError(f'Bundled IFRA NCS annex missing: {annex}')
        b = import_ncs_annex(db, annex)
        result['ncs_contributions'] = b['ncs_contributions']
        result['imported'] = True
    _set_meta(db, 'ifra_bundle_version', IFRA_BUNDLE_VERSION)
    return result


def ensure_bundled_reference_data(db, data_dir: Path | None = None) -> dict:
    data_dir = Path(data_dir or resource_data_dir())
    ifra = ensure_bundled_ifra51(db, data_dir)
    identity = install_identity_reference(db, data_dir)
    return {'ifra': ifra, 'identity': identity}


def enrich_existing_inventory_db(db) -> dict:
    """Fill blank material CAS fields from local identity/IFRA data at startup.

    Existing CAS values are never changed. This makes upgrades from v0.4 immediately
    useful without requiring the user to click a resolver button or re-paste materials.
    """
    from perfume_studio.services.transparency_lookup import lookup_saved_alias
    from perfume_studio.services.inventory_enrichment import build_ifra_name_index, enrich_name_from_ifra

    rows = db.query("SELECT id,name FROM materials WHERE cas IS NULL OR TRIM(cas)='' ORDER BY id")
    if not rows:
        return {'resolved': 0, 'unresolved': 0}
    index = build_ifra_name_index(db)
    updates = []
    for material in rows:
        name = material['name'] or ''
        saved = lookup_saved_alias(db, name)
        cas = saved['cas'] if saved else ''
        if not cas and index:
            match = enrich_name_from_ifra(name, index)
            cas = match['cas'] if match else ''
        if cas:
            updates.append((cas, material['id']))
    if updates:
        with db.connect() as conn:
            conn.executemany("UPDATE materials SET cas=? WHERE id=? AND (cas IS NULL OR TRIM(cas)='')", updates)
    return {'resolved': len(updates), 'unresolved': max(0, len(rows) - len(updates))}
