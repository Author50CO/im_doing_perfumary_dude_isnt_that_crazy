from __future__ import annotations

from perfume_studio.services.inventory_enrichment import normalize_material_name, material_name_candidates


def search_transparency_cache(db, query: str, limit: int = 100) -> list[dict]:
    q = normalize_material_name(query)
    if not q:
        return []
    tokens = [t for t in q.split() if len(t) > 1]
    results = []
    for row in db.query('SELECT alias, principal_name, cas, source, confidence, notes FROM material_aliases'):
        norm = normalize_material_name(row['alias']); principal_norm = normalize_material_name(row['principal_name'])
        if q == norm or q == principal_norm: score = 1000
        elif q in norm or q in principal_norm: score = 800
        else:
            hits=sum(1 for t in tokens if t in norm or t in principal_norm)
            if not hits: continue
            score=hits*100
        results.append((score, {'source':row['source'],'principal_name':row['principal_name'] or row['alias'],
                                'cas':row['cas'],'ncs_category':'','notes':row['notes'] or '',
                                'confidence':float(row['confidence'] or 1.0)}))
    for row in db.query('SELECT cas, principal_name, ncs_category FROM transparency_materials'):
        norm=normalize_material_name(row['principal_name'])
        if q==norm: score=950
        elif q in norm: score=750
        else:
            hits=sum(1 for t in tokens if t in norm)
            if not hits: continue
            score=hits*90
        results.append((score, {'source':'IFRA Transparency (local cache)','principal_name':row['principal_name'],
                                'cas':row['cas'],'ncs_category':row['ncs_category'] or '', 'notes':'','confidence':1.0}))
    results.sort(key=lambda x:(-x[0],normalize_material_name(x[1]['principal_name'])))
    dedup=[];seen=set()
    for _,row in results:
        key=(normalize_material_name(row['principal_name']),row['cas'])
        if key in seen: continue
        seen.add(key);dedup.append(row)
        if len(dedup)>=limit: break
    return dedup


def save_material_alias(db, alias: str, cas: str, principal_name: str = '', source: str = 'User inventory',
                        confidence: float = 1.0, notes: str = '') -> None:
    alias=(alias or '').strip();cas=(cas or '').strip()
    if not alias or not cas:return
    norm=normalize_material_name(alias)
    with db.connect() as conn:
        conn.execute('''INSERT INTO material_aliases(alias, normalized_alias, principal_name, cas, source, confidence, notes)
                        VALUES(?,?,?,?,?,?,?)
                        ON CONFLICT(normalized_alias) DO UPDATE SET
                          alias=excluded.alias, principal_name=excluded.principal_name, cas=excluded.cas,
                          source=excluded.source, confidence=excluded.confidence, notes=excluded.notes,
                          updated_at=CURRENT_TIMESTAMP''',
                     (alias,norm,principal_name or alias,cas,source,float(confidence),notes or ''))


def lookup_saved_alias(db, name: str):
    for candidate in material_name_candidates(name):
        if not candidate:continue
        rows=db.query('SELECT * FROM material_aliases WHERE normalized_alias=? LIMIT 1',(candidate,))
        if rows:return rows[0]
    return None


def resolve_local_identity(db, name: str):
    row=lookup_saved_alias(db,name)
    if row:
        return {'cas':row['cas'],'principal_name':row['principal_name'] or row['alias'],
                'source':row['source'],'confidence':float(row['confidence'] or 1.0),
                'notes':row['notes'] or '','match_type':'alias'}
    return None


def unresolved_identity_reason(name: str) -> str:
    # Deliberately generic: material-specific answers belong to searchable identity sources,
    # not hardcoded application logic.
    return 'No single high-confidence identity was found automatically. Resolve shows the closest algorithmic local/PubChem candidates.'
