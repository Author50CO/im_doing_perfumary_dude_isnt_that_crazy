from pathlib import Path
from tempfile import TemporaryDirectory
from perfume_studio.database import Database
from perfume_studio.services.formula_engine import FormulaLine, calculate_formula
from perfume_studio.services.ifra_engine import split_cas


def test_formula_engine_dilution():
    rows=calculate_formula([FormulaLine(1,'A',50,100,1,100),FormulaLine(2,'B 10%',50,10,2,100)],100)
    assert round(rows[0].formula_pct,4)==50
    assert round(rows[1].active_weight_g,4)==5
    assert round(sum(x.weight_g for x in rows),4)==100


def test_database_schema():
    with TemporaryDirectory() as td:
        db=Database(Path(td)/'x.db')
        mid=db.execute("INSERT INTO materials(name,cas) VALUES(?,?)",('Hedione','24851-98-7'))
        assert db.material_by_id(mid)['name']=='Hedione'
        cols={x['name'] for x in db.query('PRAGMA table_info(material_aliases)')}
        assert {'confidence','notes'} <= cols


def test_split_cas():
    assert split_cas('100-51-6\n1331-81-3')==['100-51-6','1331-81-3']


def test_inventory_line_predilution_parse():
    from perfume_studio.services.inventory_enrichment import parse_inventory_line
    x = parse_inventory_line('Ambroxan (20% DPG)')
    assert x.name == 'Ambroxan'
    assert x.predilution_pct == 20
    assert x.solvent == 'DPG'
    y = parse_inventory_line('1. Evernyl 10%')
    assert y.name == 'Evernyl'
    assert y.predilution_pct == 10


def test_ifra_inventory_enrichment_exact_only():
    from perfume_studio.services.inventory_enrichment import build_ifra_name_index, enrich_name_from_ifra
    with TemporaryDirectory() as td:
        db = Database(Path(td) / 'x.db')
        db.execute("INSERT INTO ifra_standards(std_key,amendment,name,cas_numbers,synonyms,standard_type) VALUES(?,?,?,?,?,?)",
                   ('x', 51, 'Benzyl alcohol', '100-51-6', 'Benzenemethanol', 'RESTRICTION'))
        idx = build_ifra_name_index(db)
        assert enrich_name_from_ifra('Benzyl Alcohol', idx)['cas'] == '100-51-6'
        assert enrich_name_from_ifra('Benzenemethanol', idx)['cas'] == '100-51-6'
        assert enrich_name_from_ifra('Completely unrelated material', idx) is None


def test_ifra_inventory_enrichment_handles_perfumery_alias_formatting():
    from perfume_studio.services.inventory_enrichment import build_ifra_name_index, enrich_name_from_ifra, material_name_candidates
    with TemporaryDirectory() as td:
        db = Database(Path(td) / 'x.db')
        db.execute("INSERT INTO ifra_standards(std_key,amendment,name,cas_numbers,synonyms,standard_type) VALUES(?,?,?,?,?,?)",
                   ('x1', 51, 'Linalool', '78-70-6', '', 'RESTRICTION'))
        db.execute("INSERT INTO ifra_standards(std_key,amendment,name,cas_numbers,synonyms,standard_type) VALUES(?,?,?,?,?,?)",
                   ('x2', 51, 'Evernyl', '4707-47-5', 'Veramoss', 'RESTRICTION'))
        idx = build_ifra_name_index(db)
        assert enrich_name_from_ifra('Linalool Synthetic', idx)['cas'] == '78-70-6'
        assert enrich_name_from_ifra('Evernyl (Veramoss)', idx)['cas'] == '4707-47-5'
        assert 'galaxolide' in material_name_candidates('Galaxolide 100 (Undiluted)')


def test_material_name_not_unique_for_predilutions():
    with TemporaryDirectory() as td:
        db = Database(Path(td) / 'x.db')
        a = db.execute("INSERT INTO materials(name,concentration_pct) VALUES(?,?)", ('Evernyl', 100))
        b = db.execute("INSERT INTO materials(name,concentration_pct) VALUES(?,?)", ('Evernyl', 10))
        assert a != b
        assert len(db.query("SELECT * FROM materials WHERE name='Evernyl'")) == 2


def test_legacy_unique_material_schema_migrates_without_breaking_formula_refs():
    import sqlite3
    with TemporaryDirectory() as td:
        path = Path(td) / 'legacy.db'
        conn = sqlite3.connect(path)
        conn.executescript('''
        PRAGMA foreign_keys=ON;
        CREATE TABLE materials (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            cas TEXT,
            material_type TEXT NOT NULL DEFAULT 'raw',
            concentration_pct REAL NOT NULL DEFAULT 100,
            parent_material_id INTEGER,
            solvent TEXT,
            density REAL,
            supplier TEXT,
            unit_cost_per_g REAL NOT NULL DEFAULT 0,
            currency TEXT NOT NULL DEFAULT 'USD',
            stock_g REAL NOT NULL DEFAULT 0,
            location TEXT,
            notes TEXT,
            FOREIGN KEY(parent_material_id) REFERENCES materials(id) ON DELETE SET NULL
        );
        CREATE TABLE formulas (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL UNIQUE, batch_g REAL DEFAULT 100, fragrance_load_pct REAL DEFAULT 20, ifra_category TEXT DEFAULT '4', notes TEXT);
        CREATE TABLE formula_items (id INTEGER PRIMARY KEY AUTOINCREMENT, formula_id INTEGER NOT NULL, material_id INTEGER NOT NULL, parts REAL DEFAULT 0, sort_order INTEGER DEFAULT 0, FOREIGN KEY(formula_id) REFERENCES formulas(id) ON DELETE CASCADE, FOREIGN KEY(material_id) REFERENCES materials(id) ON DELETE RESTRICT);
        INSERT INTO materials(id,name,concentration_pct) VALUES(1,'Evernyl',100);
        INSERT INTO formulas(id,name) VALUES(1,'Test');
        INSERT INTO formula_items(formula_id,material_id,parts) VALUES(1,1,10);
        ''')
        conn.commit(); conn.close()
        db = Database(path)
        assert db.material_by_id(1)['name'] == 'Evernyl'
        assert db.query('SELECT material_id FROM formula_items WHERE formula_id=1')[0]['material_id'] == 1
        assert db.query('PRAGMA foreign_key_check') == []
        db.execute("INSERT INTO materials(name,concentration_pct) VALUES(?,?)", ('Evernyl', 10))
        assert len(db.query("SELECT * FROM materials WHERE name='Evernyl'")) == 2


def test_bundled_reference_is_offline_and_resolves_trade_names(tmp_path):
    from perfume_studio.services.bundled_reference import ensure_bundled_reference_data, resource_data_dir
    from perfume_studio.services.transparency_lookup import lookup_saved_alias
    db=Database(tmp_path/'offline.db')
    result=ensure_bundled_reference_data(db, resource_data_dir())
    assert result['identity']['available'] >= 180
    assert db.query('SELECT COUNT(*) n FROM ifra_standards')[0]['n'] >= 250
    assert db.query('SELECT COUNT(*) n FROM ncs_contributions')[0]['n'] >= 900
    expected={
        'Hedione':'24851-98-7',
        'Ambroxan':'6790-58-5',
        'Galaxolide':'1222-05-5',
        'Iso E Super':'54464-57-2',
        'Evernyl (Veramoss)':'4707-47-5',
        'Bacdanol (Sandol)':'106185-75-5',
        'Kephalis':'36306-87-3',
        'Liffarome':'67633-96-9',
        'Canthoxal':'5462-06-6',
        'Helvetolide':'141773-73-1',
        'Methyl Pamplemousse':'67674-46-8',
        'Sweet Orange Essential Oil':'8008-57-9',
        'Frankinsence Essential Oil':'8016-36-2',
        'Clairy Sage Essential Oil':'8016-63-5',
    }
    for name, cas in expected.items():
        row=lookup_saved_alias(db,name)
        assert row is not None, name
        assert row['cas']==cas, name


def test_startup_auto_enriches_existing_blank_inventory(tmp_path):
    from perfume_studio.services.bundled_reference import ensure_bundled_reference_data, enrich_existing_inventory_db, resource_data_dir
    db=Database(tmp_path/'upgrade.db')
    ids=[]
    for name in ['Hedione','Evernyl (Veramoss)','Liffarome','Heliotrope Base']:
        ids.append(db.execute('INSERT INTO materials(name,cas) VALUES(?,?)',(name,'')))
    ensure_bundled_reference_data(db,resource_data_dir())
    result=enrich_existing_inventory_db(db)
    assert result['resolved'] >= 3
    rows={r['name']:r['cas'] for r in db.query('SELECT name,cas FROM materials')}
    assert rows['Hedione']=='24851-98-7'
    assert rows['Evernyl (Veramoss)']=='4707-47-5'
    assert rows['Liffarome']=='67633-96-9'
    # A proprietary base with no single verified CAS is intentionally not fabricated.
    assert not rows['Heliotrope Base']


def test_user_alias_override_survives_reference_reinstall(tmp_path):
    from perfume_studio.services.bundled_reference import ensure_bundled_reference_data, resource_data_dir
    from perfume_studio.services.transparency_lookup import save_material_alias, lookup_saved_alias
    db=Database(tmp_path/'alias.db')
    ensure_bundled_reference_data(db,resource_data_dir())
    save_material_alias(db,'My Supplier Hedione','24851-98-7','Methyl dihydrojasmonate','User inventory')
    ensure_bundled_reference_data(db,resource_data_dir())
    row=lookup_saved_alias(db,'My Supplier Hedione')
    assert row['source']=='User inventory'


def test_algorithmic_resolver_surfaces_close_new_identity_without_query_hardcode(tmp_path):
    from perfume_studio.services.resolution_candidates import get_resolution_candidates
    db=Database(tmp_path/'resolver.db')
    # Create an arbitrary identity after install. Generic fuzzy scoring must find it without a
    # material-specific query answer map.
    db.execute("""INSERT INTO material_aliases(alias,normalized_alias,principal_name,cas,source,confidence,notes)
                  VALUES(?,?,?,?,?,?,?)""",
               ('Heliotropin','heliotropin','Heliotropin','120-57-0','test identity',1.0,''))
    rows=get_resolution_candidates(db,'Heliotrope Base',limit=4,allow_online=False)
    assert rows
    assert rows[0]['principal_name']=='Heliotropin'
    assert rows[0]['cas']=='120-57-0'


def test_pubchem_is_automatic_name_discovery_then_local_identity(monkeypatch,tmp_path):
    import perfume_studio.services.resolution_candidates as rc
    db=Database(tmp_path/'resolver-online.db')
    db.execute("INSERT INTO ifra_standards(std_key,amendment,name,cas_numbers,synonyms,standard_type) VALUES(?,?,?,?,?,?)",
               ('x',51,'Mysteriol','123-45-6','','RESTRICTION'))

    def fake_get(url,timeout=5.0):
        if '/autocomplete/' in url:
            return {'dictionary_terms':{'compound':['Mysteriol']}}
        raise AssertionError('PUG synonym lookup should not be needed when the discovered name maps locally')
    monkeypatch.setattr(rc,'_pubchem_http_get',fake_get)
    rows=rc._pubchem_candidates(db,'Mystery Base',limit=4)
    assert any(x['cas']=='123-45-6' and 'PubChem name discovery' in x['source'] for x in rows)
    monkeypatch.setattr(rc,'_pubchem_http_get',lambda *a,**k: (_ for _ in ()).throw(AssertionError('cache expected')))
    assert rc._pubchem_candidates(db,'Mystery Base',limit=4)


def test_gcms_rel_abs_prefers_abs():
    from perfume_studio.services.gcms_importer import _rows_from_matrix
    matrix=[
        ['Formula entry','Weight (g)','% Rel','% Abs'],
        ['Hedione',10.0,20.0,5.0],
        ['Iso E Super',20.0,40.0,10.0],
    ]
    rows=_rows_from_matrix(matrix,'test')
    assert len(rows)==2
    assert rows[0].percent_rel==20.0
    assert rows[0].percent_abs==5.0
    assert rows[0].percent==5.0


def test_legacy_xml_formula_import_creates_predilution_material(tmp_path):
    from perfume_studio.services.bundled_reference import ensure_bundled_reference_data, resource_data_dir
    from perfume_studio.services.legacy_formula_xml import import_formula_xml_to_db
    db=Database(tmp_path/'xml.db')
    ensure_bundled_reference_data(db,resource_data_dir())
    xml=tmp_path/'formula.xml'
    xml.write_text('''<?xml version="1.0" encoding="utf-8"?>
<formula id="abc">
  <metadata><name>Old Formula</name><description>legacy</description></metadata>
  <inputs><target_weight>50</target_weight><desired_dilution>15</desired_dilution></inputs>
  <rows>
    <row><material>Hedione</material><part>20</part><manual_dilution></manual_dilution><parsed_dilution></parsed_dilution></row>
    <row><material>Ambroxan</material><part>5</part><manual_dilution>10</manual_dilution><parsed_dilution>10</parsed_dilution></row>
  </rows>
</formula>''',encoding='utf-8')
    r=import_formula_xml_to_db(db,xml)
    assert r['formulas']==1
    assert r['created_materials']==0
    f=db.query("SELECT * FROM formulas WHERE name='Old Formula'")[0]
    assert float(f['batch_g'])==50
    assert float(f['fragrance_load_pct'])==15
    assert db.query('SELECT COUNT(*) n FROM materials')[0]['n']==0
    items=db.query('SELECT * FROM formula_items WHERE formula_id=? ORDER BY sort_order',(f['id'],))
    assert [x['material_name'] for x in items]==['Hedione','Ambroxan']
    assert all(x['material_id'] is None for x in items)
    assert [float(x['parts']) for x in items]==[800.0,200.0]
    assert abs(sum(float(x['parts']) for x in items)-1000.0)<1e-9


def test_legacy_ocr_row_parser_uses_rightmost_rel_abs():
    from perfume_studio.services.legacy_gcms_ocr import OCRWord, parse_word_row
    words=[
        OCRWord('Hedione',10,10,80,20,95),
        OCRWord('12.50',300,10,50,20,95),
        OCRWord('25.00',400,10,50,20,95),
        OCRWord('6.25',500,10,50,20,95),
    ]
    row=parse_word_row(words)
    assert row['name']=='Hedione'
    assert row['percent_rel']==25.0
    assert row['percent_abs']==6.25


def test_formula_import_keeps_predilution_in_inventory_but_parts_in_1000(tmp_path):
    from perfume_studio.services.formula_io import store_imported_formulas, ImportedFormula, ImportedFormulaRow
    db=Database(tmp_path/'formula1000.db')
    db.execute("INSERT INTO materials(name,concentration_pct) VALUES(?,?)",('Material X',100))
    f=ImportedFormula('Imported',[ImportedFormulaRow('Material X',2,10,source_is_active=True),ImportedFormulaRow('Other',3,100,source_is_active=True)])
    out=store_imported_formulas(db,[f],create_missing_materials=True)
    assert out['formulas']==1
    mats=db.query("SELECT name,concentration_pct FROM materials WHERE name='Material X' ORDER BY concentration_pct")
    assert [float(x['concentration_pct']) for x in mats]==[100.0]
    items=db.query('SELECT fi.*,m.concentration_pct FROM formula_items fi LEFT JOIN materials m ON m.id=fi.material_id WHERE fi.formula_id=? ORDER BY fi.sort_order',(out['formula_ids'][0],))
    assert len(items)==2
    assert items[1]['material_id'] is None and items[1]['material_name']=='Other'
    assert abs(sum(float(x['parts']) for x in items)-1000.0)<1e-9
    assert abs(float(items[0]['parts'])-400.0)<1e-9
    assert float(items[0]['concentration_pct'])==100.0


def test_plain_pdf_text_formula_parser_normalizes_ani_shape():
    from perfume_studio.services.formula_io import _parse_text_weight_formula, normalize_values_to_1000
    text=("ANI 898307\n"
          "a floriental fragrance\n"
          "Grams\n"
          "Hedione® 200\n"
          "Iso E Super™ 200\n"
          "Ambergris Tincture 2% 25\n"
          "Damascone Alpha 10% 5\n"
          "Ethyl Maltol 1\n"
          "TOTAL 431\n")
    f=_parse_text_weight_formula(text,'fixture','fallback')
    assert f is not None
    assert f.name=='ANI 898307'
    assert [(x.name,x.predilution_pct,x.raw_value) for x in f.rows][2:4]==[
        ('Ambergris Tincture',2.0,25.0),('Damascone Alpha',10.0,5.0)]
    vals=normalize_values_to_1000([x.raw_value for x in f.rows])
    assert abs(sum(vals)-1000.0)<1e-9


def test_export_xml_is_parts_per_thousand_and_does_not_freeze_dilution(tmp_path):
    import xml.etree.ElementTree as ET
    from perfume_studio.services.formula_io import export_formula_xml
    db=Database(tmp_path/'export.db')
    a=db.execute("INSERT INTO materials(name,concentration_pct) VALUES(?,?)",('A',10))
    b=db.execute("INSERT INTO materials(name,concentration_pct) VALUES(?,?)",('B',100))
    fid=db.execute("INSERT INTO formulas(name,batch_g,fragrance_load_pct,ifra_category) VALUES(?,?,?,?)",('X',100,20,'4'))
    db.executemany('INSERT INTO formula_items(formula_id,material_id,parts,sort_order) VALUES(?,?,?,?)',[(fid,a,2,0),(fid,b,3,1)])
    out=tmp_path/'x.xml';export_formula_xml(db,fid,out)
    root=ET.parse(out).getroot();assert root.get('parts_format')=='per_thousand'
    rows=root.findall('./rows/row');parts=[float(r.findtext('part')) for r in rows]
    assert parts==[400.0,600.0]
    assert abs(sum(parts)-1000.0)<1e-9
    assert all((r.findtext('manual_dilution') or '')=='' for r in rows)
    assert all((r.findtext('parsed_dilution') or '')=='' for r in rows)


def test_bundled_heliotropin_identity_is_found_by_generic_fuzzy_resolver(tmp_path):
    from perfume_studio.services.bundled_reference import ensure_bundled_reference_data, resource_data_dir
    from perfume_studio.services.resolution_candidates import get_resolution_candidates
    db=Database(tmp_path/'helio.db')
    ensure_bundled_reference_data(db,resource_data_dir())
    # Heliotrope Base itself is not a stored query rule. The generic resolver strips "base" and
    # fuzzily ranks standalone identity names in the reference data.
    assert db.query("SELECT COUNT(*) n FROM material_resolution_candidates")[0]['n']==0
    rows=get_resolution_candidates(db,'Heliotrope Base',limit=4,allow_online=False)
    assert rows
    assert rows[0]['cas']=='120-57-0'
    assert rows[0]['principal_name']=='Piperonal'


def test_muscenone_resolver_surfaces_both_verified_identities(tmp_path):
    from perfume_studio.services.bundled_reference import ensure_bundled_reference_data, resource_data_dir
    from perfume_studio.services.resolution_candidates import get_resolution_candidates
    db=Database(tmp_path/'muscenone.db')
    ensure_bundled_reference_data(db,resource_data_dir())
    rows=get_resolution_candidates(db,'Muscenone',limit=4,allow_online=False)
    pairs={(r['principal_name'],r['cas']) for r in rows}
    assert ('(R,Z)-5-Muscenone','464207-51-0') in pairs
    assert ('MUSCENONE DELTA','63314-79-4') in pairs


def test_resolver_exact_cas_search_uses_local_identity_sources(tmp_path):
    from perfume_studio.services.bundled_reference import ensure_bundled_reference_data, resource_data_dir
    from perfume_studio.services.resolution_candidates import search_resolution_candidates_by_cas
    db=Database(tmp_path/'cas-search.db')
    ensure_bundled_reference_data(db,resource_data_dir())
    rows=search_resolution_candidates_by_cas(db,'63314-79-4',allow_online=False)
    assert rows
    assert any(r['principal_name']=='MUSCENONE DELTA' and r['cas']=='63314-79-4' for r in rows)
    assert search_resolution_candidates_by_cas(db,'not-a-cas',allow_online=False)==[]


def test_resolver_muscenone_keeps_relevant_variants_and_rejects_suffix_lookalikes(tmp_path):
    from perfume_studio.services.bundled_reference import ensure_bundled_reference_data, resource_data_dir
    from perfume_studio.services.resolution_candidates import get_resolution_candidates
    db=Database(tmp_path/'resolver.db')
    ensure_bundled_reference_data(db, resource_data_dir())
    rows=get_resolution_candidates(db,'Muscenone',limit=50,allow_online=False)
    pairs={(r['display_name'],r['cas']) for r in rows}
    assert ('Muscenone Delta','63314-79-4') in pairs
    assert ('(R,Z)-5-Muscenone','464207-51-0') in pairs
    labels=' '.join((r['display_name']+' '+r['principal_name']).lower() for r in rows)
    assert 'ionone gamma' not in labels
    assert 'damascenone' not in labels


def test_old_pubchem_cache_is_refiltered_after_upgrade(tmp_path):
    import json
    import perfume_studio.services.resolution_candidates as rc
    db=Database(tmp_path/'cache.db')
    payload=[
        {'display_name':'Muscenone Delta','principal_name':'MUSCENONE DELTA','cas':'63314-79-4',
         'source':'old cache','notes':'','confidence':.8,'score':.91,'match_type':'online fuzzy fallback candidate'},
        {'display_name':'Ionone Gamma','principal_name':'alpha-Isomethyl ionone','cas':'127-51-5',
         'source':'old cache','notes':'','confidence':.8,'score':.48,'match_type':'online fuzzy fallback candidate'},
    ]
    db.execute("INSERT INTO identity_search_cache(provider,normalized_query,payload_json) VALUES(?,?,?)",
               ('PubChem','muscenone',json.dumps(payload)))
    rows=rc._pubchem_candidates(db,'Muscenone',limit=50)
    assert any(r['cas']=='63314-79-4' for r in rows)
    assert not any(r['cas']=='127-51-5' for r in rows)


def test_manufacturing_plan_uses_inventory_predilution_and_outputs_mg_logic():
    from perfume_studio.services.formula_engine import FormulaLine, calculate_manufacturing_plan
    lines=[FormulaLine(1,'A',500,100,0,0),FormulaLine(2,'B',500,10,0,0)]
    plan=calculate_manufacturing_plan(lines,10,10)
    assert plan.forced_neat_count==0
    assert abs(plan.lines[0].weight_g*1000-500)<1e-9
    assert abs(plan.lines[1].weight_g*1000-5000)<1e-9
    assert abs(plan.solvent_g*1000-4500)<1e-9


def test_manufacturing_plan_forces_largest_diluted_material_neat_until_strength_fits():
    from perfume_studio.services.formula_engine import FormulaLine, calculate_manufacturing_plan
    lines=[FormulaLine(1,'Big diluted',700,10,0,0),FormulaLine(2,'Small neat',300,100,0,0)]
    plan=calculate_manufacturing_plan(lines,10,20)
    assert plan.forced_neat_count==1
    assert plan.lines[0].forced_neat
    assert plan.lines[0].used_concentration_pct==100
    assert abs(plan.lines[0].weight_g-1.4)<1e-9
    assert abs(plan.lines[1].weight_g-0.6)<1e-9
    assert abs(plan.solvent_g-8.0)<1e-9


def test_santal33_style_legacy_xml_converts_predilution_to_active_ppt(tmp_path):
    from perfume_studio.services.formula_io import import_formula_to_db
    db=Database(tmp_path/'santal.db')
    xml=tmp_path/'santal.xml'
    xml.write_text("""<?xml version='1.0'?><formula><metadata><name>Santal</name></metadata><inputs><target_weight>10</target_weight><desired_dilution>20</desired_dilution></inputs><rows>
    <row><material>Iso E Super</material><part>59.35</part><manual_dilution>100</manual_dilution></row>
    <row><material>Cashmeran</material><part>3.5</part><manual_dilution>10</manual_dilution></row>
    </rows></formula>""",encoding='utf-8')
    out=import_formula_to_db(db,xml)
    items=db.query('SELECT fi.parts,fi.material_name,fi.material_id FROM formula_items fi WHERE formula_id=? ORDER BY fi.sort_order',(out['formula_ids'][0],))
    # Legacy calculator XML already stores pure-material-equivalent parts; manual_dilution only changes weighing.
    assert len(items)==2 and all(x['material_id'] is None for x in items)
    assert [x['material_name'] for x in items]==['Iso E Super','Cashmeran']
    assert abs(float(items[0]['parts']) - 59.35/(59.35+3.5)*1000)<1e-8
    assert abs(float(items[1]['parts']) - 3.5/(59.35+3.5)*1000)<1e-8


def test_import_reuses_current_inventory_predilution_but_preserves_active_formula(tmp_path):
    from perfume_studio.services.formula_io import import_formula_to_db
    from perfume_studio.services.formula_engine import FormulaLine, calculate_manufacturing_plan
    db=Database(tmp_path/'inventory-match.db')
    existing=db.execute("INSERT INTO materials(name,concentration_pct) VALUES(?,?)",('Cashmeran',20))
    db.execute("INSERT INTO materials(name,concentration_pct) VALUES(?,?)",('Iso E Super',100))
    xml=tmp_path/'x.xml'
    xml.write_text("""<formula><metadata><name>X</name></metadata><inputs><target_weight>10</target_weight><desired_dilution>20</desired_dilution></inputs><rows>
      <row><material>Cashmeran</material><part>3.5</part><manual_dilution>10</manual_dilution></row>
      <row><material>Iso E Super</material><part>6.5</part><manual_dilution>100</manual_dilution></row>
    </rows></formula>""",encoding='utf-8')
    out=import_formula_to_db(db,xml)
    items=db.query('SELECT fi.parts,fi.material_name,m.* FROM formula_items fi JOIN materials m ON m.id=fi.material_id WHERE formula_id=? ORDER BY fi.sort_order',(out['formula_ids'][0],))
    assert int(items[0]['id'])==existing
    assert float(items[0]['concentration_pct'])==20
    assert abs(float(items[0]['parts'])-350.0)<1e-9
    lines=[FormulaLine(x['id'],x['material_name'],float(x['parts']),float(x['concentration_pct'] or 100),0,0,True) for x in items]
    plan=calculate_manufacturing_plan(lines,10,20)
    # Cashmeran active target 0.7 g, current Inventory is 20%, so weigh 3.5 g.
    assert abs(plan.lines[0].weight_g-3.5)<1e-9


def test_inventory_delete_nulls_formula_link_and_keeps_formula_name(tmp_path):
    db=Database(tmp_path/'delete.db')
    mid=db.execute("INSERT INTO materials(name,concentration_pct) VALUES(?,?)",('Cashmeran',10))
    fid=db.execute("INSERT INTO formulas(name) VALUES(?)",('F',))
    db.execute("INSERT INTO formula_items(formula_id,material_id,material_name,parts,sort_order) VALUES(?,?,?,?,?)",(fid,mid,'Cashmeran',1000,0))
    db.execute('DELETE FROM materials WHERE id=?',(mid,))
    item=db.query('SELECT * FROM formula_items WHERE formula_id=?',(fid,))[0]
    assert item['material_id'] is None
    assert item['material_name']=='Cashmeran'
    assert float(item['parts'])==1000
    assert db.query('PRAGMA foreign_key_check')==[]


def test_old_formula_items_schema_migrates_from_restrict_to_set_null(tmp_path):
    import sqlite3
    path=tmp_path/'old_fk.db'
    conn=sqlite3.connect(path)
    conn.executescript("""
    PRAGMA foreign_keys=ON;
    CREATE TABLE materials (id INTEGER PRIMARY KEY, name TEXT NOT NULL, cas TEXT, material_type TEXT DEFAULT 'raw', concentration_pct REAL DEFAULT 100, parent_material_id INTEGER, solvent TEXT, density REAL, supplier TEXT, purchase_price REAL, unit_cost_per_g REAL DEFAULT 0, currency TEXT DEFAULT 'USD', stock_g REAL DEFAULT 0, location TEXT, notes TEXT);
    CREATE TABLE formulas (id INTEGER PRIMARY KEY, name TEXT UNIQUE, batch_g REAL DEFAULT 100, fragrance_load_pct REAL DEFAULT 20, ifra_category TEXT DEFAULT '4', notes TEXT);
    CREATE TABLE formula_items (id INTEGER PRIMARY KEY, formula_id INTEGER NOT NULL, material_id INTEGER NOT NULL, parts REAL DEFAULT 0, sort_order INTEGER DEFAULT 0, FOREIGN KEY(formula_id) REFERENCES formulas(id) ON DELETE CASCADE, FOREIGN KEY(material_id) REFERENCES materials(id) ON DELETE RESTRICT);
    INSERT INTO materials(id,name) VALUES(1,'A'); INSERT INTO formulas(id,name) VALUES(1,'F'); INSERT INTO formula_items(id,formula_id,material_id,parts) VALUES(1,1,1,1000);
    """)
    conn.commit();conn.close()
    db=Database(path)
    db.execute('DELETE FROM materials WHERE id=1')
    row=db.query('SELECT * FROM formula_items WHERE id=1')[0]
    assert row['material_id'] is None and row['material_name']=='A'


def test_missing_inventory_makes_plan_incomplete_instead_of_assuming_neat():
    from perfume_studio.services.formula_engine import FormulaLine, calculate_manufacturing_plan
    lines=[FormulaLine(1,'Known',500,100,1,0,True),FormulaLine(None,'Missing',500,None,0,0,False)]
    plan=calculate_manufacturing_plan(lines,10,20)
    assert plan.missing_inventory_names==['Missing']
    assert plan.current_strength_without_solvent_pct is None
    assert plan.solvent_g is None
    assert plan.lines[1].weight_g is None and plan.lines[1].cost is None


def test_plan_reports_current_strength_solvent_and_cost():
    from perfume_studio.services.formula_engine import FormulaLine, calculate_manufacturing_plan
    plan=calculate_manufacturing_plan([FormulaLine(1,'A',1000,50,2.0,100,True)],10,20)
    assert abs(plan.weighed_material_total_g-4.0)<1e-9
    assert abs(plan.current_strength_without_solvent_pct-50.0)<1e-9
    assert abs(plan.solvent_g-6.0)<1e-9
    assert abs(plan.cost_total-4.0)<1e-9


def test_paste_formula_parser_is_active_parts_and_does_not_create_inventory(tmp_path):
    from perfume_studio.services.formula_io import parse_pasted_formula_text, store_imported_formulas
    db=Database(tmp_path/'paste.db')
    formula=parse_pasted_formula_text('Cashmeran 10%\t3.5\nIso E Super\t6.5\nTOTAL\t10','Paste')
    assert formula.rows[0].name=='Cashmeran' and formula.rows[0].source_is_active
    out=store_imported_formulas(db,[formula])
    assert db.query('SELECT COUNT(*) n FROM materials')[0]['n']==0
    items=db.query('SELECT material_name,material_id,parts FROM formula_items WHERE formula_id=? ORDER BY sort_order',(out['formula_ids'][0],))
    assert [x['material_name'] for x in items]==['Cashmeran','Iso E Super']
    assert all(x['material_id'] is None for x in items)
    assert [float(x['parts']) for x in items]==[350.0,650.0]

def test_inventory_predilutions_column_is_created_and_backfilled(tmp_path):
    db=Database(tmp_path/'preds.db')
    mid=db.execute("INSERT INTO materials(name,concentration_pct,predilutions) VALUES(?,?,?)",('A',10,'10, 20'))
    row=db.material_by_id(mid)
    assert row['predilutions']=='10, 20'
    assert float(row['concentration_pct'])==10


def test_import_preserves_original_material_name_for_unmatched_row(tmp_path):
    from perfume_studio.services.formula_io import parse_pasted_formula_text, store_imported_formulas
    db=Database(tmp_path/'original.db')
    f=parse_pasted_formula_text('Unknown Trade Name\t1\nKnown\t1','Orig')
    out=store_imported_formulas(db,[f])
    rows=db.query('SELECT material_name,original_material_name,disabled FROM formula_items WHERE formula_id=? ORDER BY sort_order',(out['formula_ids'][0],))
    assert rows[0]['material_name']=='Unknown Trade Name'
    assert rows[0]['original_material_name']=='Unknown Trade Name'
    assert int(rows[0]['disabled'])==0


def test_export_omits_disabled_row_and_renormalizes_to_1000(tmp_path):
    import xml.etree.ElementTree as ET
    from perfume_studio.services.formula_io import export_formula_xml
    db=Database(tmp_path/'export-disabled.db')
    fid=db.execute("INSERT INTO formulas(name) VALUES(?)",('F',))
    db.execute("INSERT INTO formula_items(formula_id,material_name,original_material_name,disabled,parts,sort_order) VALUES(?,?,?,?,?,?)",(fid,'A','A',0,600,0))
    db.execute("INSERT INTO formula_items(formula_id,material_name,original_material_name,disabled,parts,sort_order) VALUES(?,?,?,?,?,?)",(fid,'B','B',1,400,1))
    out=tmp_path/'f.xml';export_formula_xml(db,fid,out)
    root=ET.parse(out).getroot();rows=root.findall('./rows/row')
    assert len(rows)==1
    assert rows[0].findtext('material')=='A'
    assert abs(float(rows[0].findtext('part'))-1000.0)<1e-9


def test_material_delete_keeps_original_formula_identity_fields(tmp_path):
    db=Database(tmp_path/'delete-original.db')
    mid=db.execute("INSERT INTO materials(name,concentration_pct,predilutions) VALUES(?,?,?)",('Substitute',100,'100'))
    fid=db.execute("INSERT INTO formulas(name) VALUES(?)",('F',))
    db.execute("INSERT INTO formula_items(formula_id,material_id,material_name,original_material_name,disabled,parts,sort_order) VALUES(?,?,?,?,?,?,?)",
               (fid,mid,'Substitute','Original Missing Material',0,1000,0))
    db.execute('DELETE FROM materials WHERE id=?',(mid,))
    row=db.query('SELECT * FROM formula_items WHERE formula_id=?',(fid,))[0]
    assert row['material_id'] is None
    assert row['material_name']=='Substitute'
    assert row['original_material_name']=='Original Missing Material'


def test_formula_item_selected_predilution_column_and_import_choice(tmp_path):
    from perfume_studio.services.formula_io import parse_pasted_formula_text, store_imported_formulas
    db=Database(tmp_path/'stock-choice.db')
    mid=db.execute("INSERT INTO materials(name,concentration_pct,predilutions) VALUES(?,?,?)",('Vanillin',1,'1, 10'))
    formula=parse_pasted_formula_text('Vanillin 10%\t1\nIso E Super\t1','Stock Choice')
    # Add the second material only so the formula can still resolve both identities independently.
    db.execute("INSERT INTO materials(name,concentration_pct,predilutions) VALUES(?,?,?)",('Iso E Super',100,'100'))
    out=store_imported_formulas(db,[formula])
    row=db.query('SELECT material_id,selected_predilution_pct FROM formula_items WHERE formula_id=? ORDER BY sort_order',(out['formula_ids'][0],))[0]
    assert int(row['material_id'])==mid
    assert abs(float(row['selected_predilution_pct'])-10.0)<1e-9


def test_ifra_compliance_ratio_excess_mg_and_disabled_rows(tmp_path):
    from perfume_studio.services.ifra_engine import check_formula
    db=Database(tmp_path/'ifra-ratio.db')
    restricted=db.execute("INSERT INTO materials(name,cas,concentration_pct,predilutions) VALUES(?,?,?,?)",('Restricted','123-45-6',100,'100'))
    inert=db.execute("INSERT INTO materials(name,cas,concentration_pct,predilutions) VALUES(?,?,?,?)",('Inert','222-22-2',100,'100'))
    fid=db.execute("INSERT INTO formulas(name,batch_g,fragrance_load_pct,ifra_category) VALUES(?,?,?,?)",('F',10,100,'4'))
    db.execute("INSERT INTO formula_items(formula_id,material_id,material_name,original_material_name,disabled,parts,sort_order) VALUES(?,?,?,?,?,?,?)",(fid,restricted,'Restricted','Restricted',0,510,0))
    db.execute("INSERT INTO formula_items(formula_id,material_id,material_name,original_material_name,disabled,parts,sort_order) VALUES(?,?,?,?,?,?,?)",(fid,inert,'Inert','Inert',0,490,1))
    sid=db.execute("INSERT INTO ifra_standards(std_key,amendment,name,cas_numbers,standard_type) VALUES(?,?,?,?,?)",('test',51,'Restricted Standard','123-45-6','RESTRICTION'))
    db.execute("INSERT INTO ifra_limits(standard_id,category,max_pct,raw_value) VALUES(?,?,?,?)",(sid,'4',50,'50'))
    res=check_formula(db,fid,'4')
    assert len(res)==1
    assert abs(res[0].actual_pct-51.0)<1e-9
    assert abs(res[0].use_of_limit_pct-102.0)<1e-9
    assert abs(res[0].excess_mg(10)-100.0)<1e-9
    assert res[0].status=='OVER'

    # Disabling the restricted row removes it from the compliance calculation rather than leaving it in the denominator/source set.
    db.execute('UPDATE formula_items SET disabled=1 WHERE formula_id=? AND material_id=?',(fid,restricted))
    assert check_formula(db,fid,'4')==[]


def test_formula_txt_export_is_human_readable_normalized_and_has_note(tmp_path):
    from decimal import Decimal
    from perfume_studio.services.formula_text import formula_text_from_db, write_formula_txt
    db=Database(tmp_path/'txt.db')
    fid=db.execute("INSERT INTO formulas(name,batch_g,fragrance_load_pct,ifra_category,notes) VALUES(?,?,?,?,?)",('Test Formula',10,20,'4','asdfasdfasdf'))
    db.executemany("INSERT INTO formula_items(formula_id,material_name,original_material_name,parts,sort_order) VALUES(?,?,?,?,?)",[
        (fid,'Hedione','Hedione',18,0),(fid,'Hedione HC','Hedione HC',17,1),(fid,'Linalyl Acetate','Linalyl Acetate',9,2)
    ])
    text=formula_text_from_db(db,fid)
    assert text.startswith('title - Test Formula\n')
    assert 'note - asdfasdfasdf' in text
    recipe=[]
    for line in text.splitlines():
        if ' - ' in line and not line.lower().startswith(('title -','note -')):
            recipe.append(Decimal(line.split(' - ',1)[0]))
    assert sum(recipe)==Decimal('1000')
    out=write_formula_txt(db,fid,tmp_path)
    assert out==tmp_path/'Test Formula.txt'
    assert out.read_text(encoding='utf-8')==text


def test_formula_txt_backup_name_and_duplicate_suffix(tmp_path):
    from datetime import datetime
    from perfume_studio.services.formula_text import backup_formula_txt
    db=Database(tmp_path/'backup.db')
    fid=db.execute("INSERT INTO formulas(name,notes) VALUES(?,?)",('Title','old'))
    db.execute("INSERT INTO formula_items(formula_id,material_name,original_material_name,parts) VALUES(?,?,?,?)",(fid,'A','A',1000))
    now=datetime(2026,8,12,12,0,0)
    a=backup_formula_txt(db,fid,tmp_path,now)
    b=backup_formula_txt(db,fid,tmp_path,now)
    assert a.name=='Title-2026-aug-12 backup.txt'
    assert b.name=='Title-2026-aug-12 backup 2.txt'
    assert '1000 - A' in a.read_text(encoding='utf-8')


def test_txt_formula_roundtrip_and_same_name_overwrite(tmp_path):
    from perfume_studio.services.formula_io import import_formula_file, store_imported_formulas
    p=tmp_path/'recipe.txt'
    p.write_text('title - X\n180 - Hedione\n170 - Hedione HC\n90 - Linalyl Acetate\n\nnote - first\n',encoding='utf-8')
    formulas=import_formula_file(p)
    assert formulas[0].name=='X' and formulas[0].notes=='first'
    db=Database(tmp_path/'roundtrip.db')
    first=store_imported_formulas(db,formulas,overwrite_existing=True)
    fid=first['formula_ids'][0]
    p.write_text('title - X\n900 - Iso E Super\n100 - Hedione\n\nnote - replaced\n',encoding='utf-8')
    second=store_imported_formulas(db,import_formula_file(p),overwrite_existing=True)
    assert second['formula_ids']==[fid]
    assert second['overwritten']==['X']
    rows=db.query('SELECT material_name,parts FROM formula_items WHERE formula_id=? ORDER BY sort_order',(fid,))
    assert [r['material_name'] for r in rows]==['Iso E Super','Hedione']
    assert db.query('SELECT notes FROM formulas WHERE id=?',(fid,))[0]['notes']=='replaced'


def test_material_reference_ifra_details_uses_local_cat4(tmp_path):
    from perfume_studio.services.material_reference import ifra_details_for_cas
    db=Database(tmp_path/'ref.db')
    sid=db.execute("INSERT INTO ifra_standards(std_key,amendment,name,cas_numbers,standard_type) VALUES(?,?,?,?,?)",('x',51,'Test substance','100-51-6','RESTRICTION'))
    db.execute("INSERT INTO ifra_limits(standard_id,category,max_pct,raw_value) VALUES(?,?,?,?)",(sid,'4',2.5,'2.5'))
    result=ifra_details_for_cas(db,'100-51-6')
    assert result['standards'][0]['name']=='Test substance'
    assert result['standards'][0]['max_pct']==2.5
    assert 'Cat 4 max 2.5%' in result['summary']


def test_pubchem_density_parser_normalizes_units():
    from perfume_studio.services.material_reference import _density_value_g_ml
    assert _density_value_g_ml('1.025 g/mL at 25 °C') == 1.025
    assert _density_value_g_ml('1.025 g/cm3 at 20 C') == 1.025
    assert _density_value_g_ml('1025 kg/m3 at 20 C') == 1.025


def test_pubchem_density_conflict_is_not_auto_selected():
    from perfume_studio.services.material_reference import _preferred_density
    rows=[
        {'g_ml': 1.0, 'temperature_c': 25.0, 'raw': '1.0 g/mL at 25 C'},
        {'g_ml': 1.2, 'temperature_c': 25.0, 'raw': '1.2 g/mL at 25 C'},
    ]
    assert _preferred_density(rows) is None


def test_note_groups_are_reusable_and_material_assignments_cascade(tmp_path):
    db=Database(tmp_path/'notes.db')
    mid=db.execute("INSERT INTO materials(name) VALUES(?)",('Hedione',))
    gid=db.execute("INSERT INTO note_groups(name,position,color_hex) VALUES(?,?,?)",('Fruity','Top','#55aa66'))
    db.execute("INSERT INTO material_note_groups(material_id,note_group_id) VALUES(?,?)",(mid,gid))
    row=db.query('''SELECT ng.name,ng.position,ng.color_hex FROM material_note_groups mng
                    JOIN note_groups ng ON ng.id=mng.note_group_id WHERE mng.material_id=?''',(mid,))[0]
    assert row['name']=='Fruity'
    assert row['position']=='Top'
    assert row['color_hex']=='#55aa66'
    db.execute('DELETE FROM materials WHERE id=?',(mid,))
    assert db.query('SELECT * FROM material_note_groups')==[]
    assert db.query('SELECT * FROM note_groups WHERE id=?',(gid,))


def test_comptox_density_summary_prefers_experimental_then_predicted():
    from perfume_studio.services.material_reference import _parse_comptox_density_summary
    exp=_parse_comptox_density_summary('<div>Density 0.947 (2) 0.981 (2) 0.947 0.981 0.94 to 0.95 0.97 to 0.99 g/cm^3</div>')
    assert exp['type']=='experimental' and abs(exp['g_ml']-0.947)<1e-9
    pred=_parse_comptox_density_summary('<div>Density - 1.22 (2) - 1.22 - 1.21 to 1.23 g/cm^3</div>')
    assert pred['type']=='predicted' and abs(pred['g_ml']-1.22)<1e-9


def test_comptox_dtxsid_can_be_reused_from_pubchem_synonyms(monkeypatch):
    import perfume_studio.services.material_reference as mr
    rec={'synonyms':['Example material','DTXSID7020182']}
    # Should never need a network search when the PubChem synonym cache already carries DTXSID.
    monkeypatch.setattr(mr.requests,'get',lambda *a,**k: (_ for _ in ()).throw(AssertionError('network should not be used')))
    assert mr._find_dtxsid_from_comptox('80-05-7',rec)=='DTXSID7020182'


def test_reference_snapshot_persists_comptox_density(tmp_path):
    from perfume_studio.services.material_reference import save_reference_snapshot
    db=Database(tmp_path/'ref.db')
    mid=db.execute("INSERT INTO materials(name,cas) VALUES(?,?)",('Test','80-05-7'))
    save_reference_snapshot(db,mid,comptox={'dtxsid':'DTXSID7020182','density_g_ml':1.03,'density_type':'experimental','raw':'EPA test','fetched_at':'2026-08-12T12:00:00'})
    row=db.query('SELECT * FROM material_details WHERE material_id=?',(mid,))[0]
    assert row['comptox_dtxsid']=='DTXSID7020182'
    assert abs(row['comptox_density']-1.03)<1e-9
    assert row['comptox_density_type']=='experimental'


def test_manual_override_controls_weight_and_uses_selected_inventory_price():
    from perfume_studio.services.formula_engine import FormulaLine, calculate_manufacturing_plan
    # 10 g finished perfume at 20% active = 2 g active material.
    # Selected inventory row is 10%, but a manual 50% manufacturing override should weigh 4 g.
    line=FormulaLine(1,'Test Material',1000,10,5.0,100,True,50)
    plan=calculate_manufacturing_plan([line],10,20,{},2.0)
    assert round(plan.lines[0].weight_g,6)==4.0
    assert round(plan.lines[0].used_concentration_pct,6)==50.0
    # Purchase price/g is for the 100% raw material: 2 g active * 5 = 10.00.
    assert plan.lines[0].cost==10.00
    # Practical added solvent is 6 g, but solvent COST is based on the requested finished
    # strength: 10 g at 20% strength => 8 g solvent fraction * 2/g = 16.00.
    assert plan.solvent_g==6.0
    assert plan.solvent_cost_basis_g==8.0
    assert plan.material_cost_total==10.00
    assert plan.solvent_cost==16.00
    assert plan.cost_total==26.00
    assert plan.missing_neat_names==[]


def test_auto_100_override_does_not_require_neat_price_row():
    from perfume_studio.services.formula_engine import FormulaLine, calculate_manufacturing_plan
    # At 10% this would require 20 g stock for a 10 g finished batch, so auto override to 100%.
    line=FormulaLine(1,'Test Material',1000,10,5.0,100,True,None)
    plan=calculate_manufacturing_plan([line],10,20,{},0.5)
    row=plan.lines[0]
    assert row.forced_neat is True
    assert row.used_concentration_pct==100.0
    assert row.weight_g==2.0
    # Still use the chosen Inventory row's 5/g rate instead of demanding a separate neat price.
    assert row.cost==10.00
    assert plan.missing_neat_names==[]
    assert plan.solvent_g==8.0
    assert plan.solvent_cost==4.00
    assert plan.cost_total==14.00


def test_formula_items_schema_persists_manual_override(tmp_path):
    db=Database(tmp_path/'override.db')
    cols={x['name'] for x in db.query('PRAGMA table_info(formula_items)')}
    assert 'manual_override_pct' in cols
    fid=db.execute("INSERT INTO formulas(name) VALUES(?)",('Override Test',))
    db.execute("""INSERT INTO formula_items(formula_id,material_name,original_material_name,manual_override_pct,parts,sort_order)
                  VALUES(?,?,?,?,?,?)""",(fid,'A','A',25,1000,0))
    row=db.query('SELECT manual_override_pct FROM formula_items WHERE formula_id=?',(fid,))[0]
    assert float(row['manual_override_pct'])==25.0

def test_prediluted_stock_cost_uses_active_raw_material_grams():
    from perfume_studio.services.formula_engine import FormulaLine, calculate_manufacturing_plan
    # 10 g of a 10% working stock contains 1 g active raw material.
    # Raw material purchase cost is 100 per g, so ingredient cost must be 100.
    line=FormulaLine(1,'Coumarin',1000,10,100.0,100,True,None)
    plan=calculate_manufacturing_plan([line],10,10,{},0.0)
    assert round(plan.lines[0].weight_g,6)==10.0
    assert round(plan.lines[0].active_weight_g,6)==1.0
    assert plan.lines[0].cost==100.00
    assert plan.material_cost_total==100.00
