from __future__ import annotations
import sqlite3
from pathlib import Path
from contextlib import contextmanager

SCHEMA = r'''
PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS materials (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    cas TEXT,
    material_type TEXT NOT NULL DEFAULT 'raw',
    concentration_pct REAL NOT NULL DEFAULT 100,
    predilutions TEXT,
    parent_material_id INTEGER,
    solvent TEXT,
    density REAL,
    supplier TEXT,
    purchase_price REAL,
    unit_cost_per_g REAL NOT NULL DEFAULT 0,
    currency TEXT NOT NULL DEFAULT 'USD',
    stock_g REAL NOT NULL DEFAULT 0,
    location TEXT,
    notes TEXT,
    FOREIGN KEY(parent_material_id) REFERENCES materials(id) ON DELETE SET NULL
);
CREATE INDEX IF NOT EXISTS idx_materials_cas ON materials(cas);

CREATE TABLE IF NOT EXISTS material_details (
    material_id INTEGER PRIMARY KEY,
    note_position TEXT NOT NULL DEFAULT '',
    odor_notes TEXT,
    pubchem_cid INTEGER,
    pubchem_title TEXT,
    pubchem_iupac_name TEXT,
    molecular_formula TEXT,
    molecular_weight TEXT,
    canonical_smiles TEXT,
    isomeric_smiles TEXT,
    inchikey TEXT,
    pubchem_synonyms TEXT,
    pubchem_description TEXT,
    pubchem_density REAL,
    pubchem_density_raw TEXT,
    pubchem_fetched_at TEXT,
    comptox_dtxsid TEXT,
    comptox_density REAL,
    comptox_density_type TEXT,
    comptox_density_raw TEXT,
    comptox_fetched_at TEXT,
    ifra_summary TEXT,
    ifra_fetched_at TEXT,
    FOREIGN KEY(material_id) REFERENCES materials(id) ON DELETE CASCADE
);


CREATE TABLE IF NOT EXISTS note_groups (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE COLLATE NOCASE,
    position TEXT NOT NULL DEFAULT '',
    color_hex TEXT NOT NULL DEFAULT '#d9d9d9',
    sort_order INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS material_note_groups (
    material_id INTEGER NOT NULL,
    note_group_id INTEGER NOT NULL,
    PRIMARY KEY(material_id,note_group_id),
    FOREIGN KEY(material_id) REFERENCES materials(id) ON DELETE CASCADE,
    FOREIGN KEY(note_group_id) REFERENCES note_groups(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_material_note_groups_material ON material_note_groups(material_id);

CREATE TABLE IF NOT EXISTS formulas (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    batch_g REAL NOT NULL DEFAULT 100,
    fragrance_load_pct REAL NOT NULL DEFAULT 20,
    ifra_category TEXT NOT NULL DEFAULT '4',
    notes TEXT
);

CREATE TABLE IF NOT EXISTS formula_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    formula_id INTEGER NOT NULL,
    material_id INTEGER,
    material_name TEXT NOT NULL DEFAULT '',
    original_material_name TEXT NOT NULL DEFAULT '',
    disabled INTEGER NOT NULL DEFAULT 0,
    selected_predilution_pct REAL,
    manual_override_pct REAL,
    parts REAL NOT NULL DEFAULT 0,
    sort_order INTEGER NOT NULL DEFAULT 0,
    FOREIGN KEY(formula_id) REFERENCES formulas(id) ON DELETE CASCADE,
    FOREIGN KEY(material_id) REFERENCES materials(id) ON DELETE SET NULL
);
CREATE INDEX IF NOT EXISTS idx_formula_items_formula ON formula_items(formula_id);

CREATE TABLE IF NOT EXISTS ifra_standards (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    std_key TEXT UNIQUE,
    amendment INTEGER,
    name TEXT NOT NULL,
    cas_numbers TEXT,
    synonyms TEXT,
    standard_type TEXT,
    risk_property TEXT,
    notes TEXT
);
CREATE TABLE IF NOT EXISTS ifra_limits (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    standard_id INTEGER NOT NULL,
    category TEXT NOT NULL,
    max_pct REAL,
    raw_value TEXT,
    UNIQUE(standard_id, category),
    FOREIGN KEY(standard_id) REFERENCES ifra_standards(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_ifra_limits_category ON ifra_limits(category);

CREATE TABLE IF NOT EXISTS ncs_contributions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    amendment INTEGER,
    ncs_name TEXT,
    botanical_name TEXT,
    principal_cas TEXT,
    other_cas TEXT,
    constituent_name TEXT,
    constituent_cas TEXT,
    concentration_pct REAL NOT NULL,
    UNIQUE(ncs_name, principal_cas, constituent_cas, concentration_pct)
);
CREATE INDEX IF NOT EXISTS idx_ncs_principal_cas ON ncs_contributions(principal_cas);
CREATE INDEX IF NOT EXISTS idx_ncs_constituent_cas ON ncs_contributions(constituent_cas);

CREATE TABLE IF NOT EXISTS transparency_materials (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    cas TEXT NOT NULL,
    principal_name TEXT NOT NULL,
    ncs_category TEXT,
    source_url TEXT,
    cached_at TEXT,
    UNIQUE(cas, principal_name)
);
CREATE INDEX IF NOT EXISTS idx_transparency_name ON transparency_materials(principal_name);
CREATE INDEX IF NOT EXISTS idx_transparency_cas ON transparency_materials(cas);

CREATE TABLE IF NOT EXISTS material_aliases (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    alias TEXT NOT NULL,
    normalized_alias TEXT NOT NULL UNIQUE,
    principal_name TEXT,
    cas TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'manual',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_material_alias_cas ON material_aliases(cas);

CREATE TABLE IF NOT EXISTS material_resolution_candidates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    query_alias TEXT NOT NULL,
    normalized_query TEXT NOT NULL,
    display_name TEXT NOT NULL,
    principal_name TEXT,
    cas TEXT NOT NULL,
    source TEXT NOT NULL,
    rank REAL NOT NULL DEFAULT 1,
    notes TEXT,
    UNIQUE(normalized_query, display_name, cas)
);
CREATE INDEX IF NOT EXISTS idx_resolution_candidate_query ON material_resolution_candidates(normalized_query);


CREATE TABLE IF NOT EXISTS identity_search_cache (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    provider TEXT NOT NULL,
    normalized_query TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    fetched_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(provider, normalized_query)
);
CREATE INDEX IF NOT EXISTS idx_identity_search_cache_query ON identity_search_cache(normalized_query);

CREATE TABLE IF NOT EXISTS app_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
'''

class Database:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.initialize()

    @contextmanager
    def connect(self):
        # A small timeout plus SQLite busy_timeout makes short overlapping autosave/read
        # operations wait instead of surfacing as an intermittent 'database is locked' error.
        conn = sqlite3.connect(self.path, timeout=10.0)
        conn.row_factory = sqlite3.Row
        conn.execute('PRAGMA foreign_keys = ON')
        conn.execute('PRAGMA busy_timeout = 5000')
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def initialize(self):
        with self.connect() as conn:
            # WAL is considerably more tolerant of UI reads occurring around an autosave write.
            # NORMAL synchronous mode keeps the database durable enough for a local desktop app
            # while avoiding unnecessary UI stalls.
            conn.execute('PRAGMA journal_mode = WAL')
            conn.execute('PRAGMA synchronous = NORMAL')
            conn.executescript(SCHEMA)
            # Lightweight forward migrations for existing user databases.
            columns = {row['name'] for row in conn.execute('PRAGMA table_info(materials)').fetchall()}
            if 'purchase_price' not in columns:
                conn.execute('ALTER TABLE materials ADD COLUMN purchase_price REAL')
            detail_columns = {row['name'] for row in conn.execute('PRAGMA table_info(material_details)').fetchall()}
            if 'pubchem_density' not in detail_columns:
                conn.execute('ALTER TABLE material_details ADD COLUMN pubchem_density REAL')
            if 'pubchem_density_raw' not in detail_columns:
                conn.execute('ALTER TABLE material_details ADD COLUMN pubchem_density_raw TEXT')
            for col, decl in [
                ('comptox_dtxsid','TEXT'), ('comptox_density','REAL'), ('comptox_density_type','TEXT'),
                ('comptox_density_raw','TEXT'), ('comptox_fetched_at','TEXT')
            ]:
                if col not in detail_columns:
                    conn.execute(f'ALTER TABLE material_details ADD COLUMN {col} {decl}')
            alias_columns = {row['name'] for row in conn.execute('PRAGMA table_info(material_aliases)').fetchall()}
            if 'confidence' not in alias_columns:
                conn.execute('ALTER TABLE material_aliases ADD COLUMN confidence REAL NOT NULL DEFAULT 1.0')
            if 'notes' not in alias_columns:
                conn.execute('ALTER TABLE material_aliases ADD COLUMN notes TEXT')
            # Older databases only stored cost/g and stock. Back-fill a package price
            # so the new spreadsheet can display something sensible without losing data.
            conn.execute('''UPDATE materials
                            SET purchase_price = unit_cost_per_g * stock_g
                            WHERE purchase_price IS NULL
                              AND unit_cost_per_g IS NOT NULL
                              AND stock_g IS NOT NULL
                              AND stock_g > 0''')
            # This application is intentionally Category 4-only. Keep the legacy
            # column for schema compatibility, but normalize all existing formulas.
            conn.execute("UPDATE formulas SET ifra_category='4' WHERE ifra_category IS NULL OR ifra_category<>'4'")
            conn.execute("UPDATE material_details SET note_position='Mid' WHERE LOWER(TRIM(note_position))='middle'")
        self._remove_legacy_material_name_unique()
        self._upgrade_formula_items_decoupled()
        with self.connect() as conn:
            columns = {row['name'] for row in conn.execute('PRAGMA table_info(materials)').fetchall()}
            if 'predilutions' not in columns:
                conn.execute('ALTER TABLE materials ADD COLUMN predilutions TEXT')
            conn.execute("""UPDATE materials SET predilutions=CAST(concentration_pct AS TEXT)
                            WHERE predilutions IS NULL OR TRIM(predilutions)=''""")
            fi_columns = {row['name'] for row in conn.execute('PRAGMA table_info(formula_items)').fetchall()}
            if 'original_material_name' not in fi_columns:
                conn.execute("ALTER TABLE formula_items ADD COLUMN original_material_name TEXT NOT NULL DEFAULT ''")
            if 'disabled' not in fi_columns:
                conn.execute('ALTER TABLE formula_items ADD COLUMN disabled INTEGER NOT NULL DEFAULT 0')
            if 'selected_predilution_pct' not in fi_columns:
                conn.execute('ALTER TABLE formula_items ADD COLUMN selected_predilution_pct REAL')
            if 'manual_override_pct' not in fi_columns:
                conn.execute('ALTER TABLE formula_items ADD COLUMN manual_override_pct REAL')
            conn.execute("""UPDATE formula_items SET original_material_name=material_name
                            WHERE TRIM(COALESCE(original_material_name,''))=''""")

    def _remove_legacy_material_name_unique(self):
        """Upgrade v0.1 databases where materials.name was UNIQUE.

        The spreadsheet inventory needs to allow, for example, both a neat material and
        a 10% predilution with the same visible name. Formula items reference materials by
        numeric id, so ids are preserved during this table rebuild.
        """
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        try:
            indexes = conn.execute('PRAGMA index_list(materials)').fetchall()
            has_legacy_unique = any(int(row['unique']) == 1 and row['origin'] == 'u' for row in indexes)
            if not has_legacy_unique:
                return
            conn.execute('PRAGMA foreign_keys = OFF')
            conn.executescript('''
                BEGIN;
                CREATE TABLE materials_new (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    cas TEXT,
                    material_type TEXT NOT NULL DEFAULT 'raw',
                    concentration_pct REAL NOT NULL DEFAULT 100,
                    predilutions TEXT,
                    parent_material_id INTEGER,
                    solvent TEXT,
                    density REAL,
                    supplier TEXT,
                    purchase_price REAL,
                    unit_cost_per_g REAL NOT NULL DEFAULT 0,
                    currency TEXT NOT NULL DEFAULT 'USD',
                    stock_g REAL NOT NULL DEFAULT 0,
                    location TEXT,
                    notes TEXT,
                    FOREIGN KEY(parent_material_id) REFERENCES materials(id) ON DELETE SET NULL
                );
                INSERT INTO materials_new(
                    id,name,cas,material_type,concentration_pct,predilutions,parent_material_id,solvent,density,
                    supplier,purchase_price,unit_cost_per_g,currency,stock_g,location,notes
                )
                SELECT
                    id,name,cas,material_type,concentration_pct,CAST(concentration_pct AS TEXT),parent_material_id,solvent,density,
                    supplier,purchase_price,unit_cost_per_g,currency,stock_g,location,notes
                FROM materials;
                DROP TABLE materials;
                ALTER TABLE materials_new RENAME TO materials;
                CREATE INDEX IF NOT EXISTS idx_materials_cas ON materials(cas);

CREATE TABLE IF NOT EXISTS material_details (
    material_id INTEGER PRIMARY KEY,
    note_position TEXT NOT NULL DEFAULT '',
    odor_notes TEXT,
    pubchem_cid INTEGER,
    pubchem_title TEXT,
    pubchem_iupac_name TEXT,
    molecular_formula TEXT,
    molecular_weight TEXT,
    canonical_smiles TEXT,
    isomeric_smiles TEXT,
    inchikey TEXT,
    pubchem_synonyms TEXT,
    pubchem_description TEXT,
    pubchem_density REAL,
    pubchem_density_raw TEXT,
    pubchem_fetched_at TEXT,
    comptox_dtxsid TEXT,
    comptox_density REAL,
    comptox_density_type TEXT,
    comptox_density_raw TEXT,
    comptox_fetched_at TEXT,
    ifra_summary TEXT,
    ifra_fetched_at TEXT,
    FOREIGN KEY(material_id) REFERENCES materials(id) ON DELETE CASCADE
);
                COMMIT;
            ''')
        except Exception:
            try:
                conn.execute('ROLLBACK')
            except Exception:
                pass
            raise
        finally:
            conn.close()


    def _upgrade_formula_items_decoupled(self):
        """Decouple formula identity from Inventory rows.

        Formula rows keep their own material_name and only optionally link to an Inventory id.
        Deleting an Inventory row therefore leaves the formula intact and NULLs material_id.
        """
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        try:
            cols = conn.execute('PRAGMA table_info(formula_items)').fetchall()
            names = {r['name'] for r in cols}
            material_col = next((r for r in cols if r['name'] == 'material_id'), None)
            fks = conn.execute('PRAGMA foreign_key_list(formula_items)').fetchall()
            mat_fk = next((r for r in fks if r['from'] == 'material_id'), None)
            needs = (
                'material_name' not in names
                or 'original_material_name' not in names
                or 'disabled' not in names
                or (material_col is not None and int(material_col['notnull']) != 0)
                or mat_fk is None
                or str(mat_fk['on_delete']).upper() != 'SET NULL'
            )
            if not needs:
                conn.execute("""UPDATE formula_items
                                SET material_name=COALESCE((SELECT name FROM materials WHERE id=formula_items.material_id),'')
                                WHERE TRIM(COALESCE(material_name,''))=''""")
                conn.execute("""UPDATE formula_items SET original_material_name=material_name
                                WHERE TRIM(COALESCE(original_material_name,''))=''""")
                conn.commit()
                return

            conn.execute('PRAGMA foreign_keys = OFF')
            conn.execute('BEGIN')
            conn.execute("""CREATE TABLE formula_items_new (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                formula_id INTEGER NOT NULL,
                material_id INTEGER,
                material_name TEXT NOT NULL DEFAULT '',
                original_material_name TEXT NOT NULL DEFAULT '',
                disabled INTEGER NOT NULL DEFAULT 0,
                selected_predilution_pct REAL,
                manual_override_pct REAL,
                parts REAL NOT NULL DEFAULT 0,
                sort_order INTEGER NOT NULL DEFAULT 0,
                FOREIGN KEY(formula_id) REFERENCES formulas(id) ON DELETE CASCADE,
                FOREIGN KEY(material_id) REFERENCES materials(id) ON DELETE SET NULL
            )""")
            has_original = 'original_material_name' in names
            has_disabled = 'disabled' in names
            has_selected_pred = 'selected_predilution_pct' in names
            has_manual_override = 'manual_override_pct' in names
            if 'material_name' in names:
                original_expr = "COALESCE(NULLIF(TRIM(fi.original_material_name),''),NULLIF(TRIM(fi.material_name),''),m.name,'')" if has_original else "COALESCE(NULLIF(TRIM(fi.material_name),''),m.name,'')"
                disabled_expr = "COALESCE(fi.disabled,0)" if has_disabled else "0"
                selected_pred_expr = "fi.selected_predilution_pct" if has_selected_pred else "NULL"
                manual_override_expr = "fi.manual_override_pct" if has_manual_override else "NULL"
                conn.execute(f"""INSERT INTO formula_items_new(id,formula_id,material_id,material_name,original_material_name,disabled,selected_predilution_pct,manual_override_pct,parts,sort_order)
                                SELECT fi.id,fi.formula_id,fi.material_id,
                                       COALESCE(NULLIF(TRIM(fi.material_name),''),m.name,''),
                                       {original_expr},{disabled_expr},{selected_pred_expr},{manual_override_expr},fi.parts,fi.sort_order
                                FROM formula_items fi LEFT JOIN materials m ON m.id=fi.material_id""")
            else:
                conn.execute("""INSERT INTO formula_items_new(id,formula_id,material_id,material_name,original_material_name,disabled,selected_predilution_pct,manual_override_pct,parts,sort_order)
                                SELECT fi.id,fi.formula_id,fi.material_id,COALESCE(m.name,''),COALESCE(m.name,''),0,NULL,NULL,fi.parts,fi.sort_order
                                FROM formula_items fi LEFT JOIN materials m ON m.id=fi.material_id""")
            conn.execute('DROP TABLE formula_items')
            conn.execute('ALTER TABLE formula_items_new RENAME TO formula_items')
            conn.execute('CREATE INDEX IF NOT EXISTS idx_formula_items_formula ON formula_items(formula_id)')
            conn.execute('COMMIT')
        except Exception:
            try:
                conn.execute('ROLLBACK')
            except Exception:
                pass
            raise
        finally:
            try:
                conn.execute('PRAGMA foreign_keys = ON')
            except Exception:
                pass
            conn.close()

    def query(self, sql: str, params=()):
        with self.connect() as conn:
            return conn.execute(sql, params).fetchall()

    def execute(self, sql: str, params=()):
        with self.connect() as conn:
            cur = conn.execute(sql, params)
            return cur.lastrowid

    def executemany(self, sql: str, rows):
        with self.connect() as conn:
            conn.executemany(sql, rows)

    def material_by_id(self, material_id: int):
        rows = self.query('SELECT * FROM materials WHERE id=?', (material_id,))
        return rows[0] if rows else None

    def material_by_cas(self, cas: str):
        if not cas:
            return None
        rows = self.query('SELECT * FROM materials WHERE TRIM(cas)=TRIM(?) LIMIT 1', (cas,))
        return rows[0] if rows else None

    def list_materials(self):
        return self.query('SELECT * FROM materials ORDER BY name COLLATE NOCASE')
