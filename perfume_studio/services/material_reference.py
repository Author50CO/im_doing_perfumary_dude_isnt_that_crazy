from __future__ import annotations

from datetime import datetime
import json
import re
import os
import html as html_lib
from urllib.parse import quote

import requests

from perfume_studio.services.ifra_engine import split_cas

CAS_RE = re.compile(r'\b\d{2,7}-\d{2}-\d\b')
DTXSID_RE = re.compile(r'\bDTXSID\d+\b', re.I)


def cas_tokens(text: str) -> list[str]:
    return list(dict.fromkeys(CAS_RE.findall(str(text or ''))))


def ifra_details_for_cas(db, cas_text: str) -> dict:
    wanted = set(cas_tokens(cas_text))
    if not wanted:
        return {'standards': [], 'ncs': [], 'summary': 'No CAS number is available for IFRA lookup.'}

    standards = []
    for row in db.query('SELECT * FROM ifra_standards ORDER BY name COLLATE NOCASE'):
        row_cas = set(split_cas(row['cas_numbers'] or ''))
        overlap = wanted & row_cas
        if not overlap:
            continue
        limit_rows = db.query('SELECT max_pct,raw_value FROM ifra_limits WHERE standard_id=? AND category=?', (row['id'], '4'))
        max_pct = limit_rows[0]['max_pct'] if limit_rows else None
        raw_value = limit_rows[0]['raw_value'] if limit_rows else ''
        standards.append({
            'name': row['name'], 'cas': '; '.join(sorted(overlap)), 'type': row['standard_type'] or '',
            'risk_property': row['risk_property'] or '', 'max_pct': max_pct, 'raw_value': raw_value or '',
            'notes': row['notes'] or '',
        })

    ncs = []
    for row in db.query('SELECT * FROM ncs_contributions ORDER BY ncs_name COLLATE NOCASE, constituent_name COLLATE NOCASE'):
        principal = set(cas_tokens(row['principal_cas'] or '')) | set(cas_tokens(row['other_cas'] or ''))
        if wanted & principal:
            ncs.append({
                'ncs_name': row['ncs_name'] or '', 'botanical_name': row['botanical_name'] or '',
                'constituent_name': row['constituent_name'] or '', 'constituent_cas': row['constituent_cas'] or '',
                'concentration_pct': float(row['concentration_pct'] or 0),
            })

    lines = []
    if standards:
        lines.append('IFRA 51 Standards (Category 4)')
        for x in standards:
            if x['max_pct'] is None:
                limit = x['raw_value'] or 'No numeric Cat 4 maximum in overview'
            else:
                limit = f"Cat 4 max {float(x['max_pct']):g}%"
            extra = ' | '.join(v for v in (x['type'], x['risk_property']) if v)
            lines.append(f"• {x['name']} — {limit}" + (f' | {extra}' if extra else ''))
    else:
        lines.append('No direct IFRA 51 Standard matched this CAS.')
    if ncs:
        lines.append('')
        lines.append(f'NCS contribution rows: {len(ncs)}')
        for x in ncs[:80]:
            bot = f" ({x['botanical_name']})" if x['botanical_name'] else ''
            lines.append(f"• {x['ncs_name']}{bot}: {x['constituent_name']} {x['concentration_pct']:g}%")
        if len(ncs) > 80:
            lines.append(f'… {len(ncs)-80} more contribution rows')
    return {'standards': standards, 'ncs': ncs, 'summary': '\n'.join(lines)}


def _get_json(url: str, timeout: float = 10.0) -> dict:
    response = requests.get(url, timeout=timeout, headers={'User-Agent': 'PerfumeStudio/0.9.10'})
    response.raise_for_status()
    return response.json()



def _walk_pubchem_strings(obj):
    """Yield human-readable strings from a PUG-View JSON subtree."""
    if isinstance(obj, dict):
        # PUG-View annotations commonly use StringWithMarkup, String, Number + Unit.
        swm = obj.get('StringWithMarkup')
        if isinstance(swm, list):
            for item in swm:
                if isinstance(item, dict) and item.get('String'):
                    yield str(item['String'])
        if isinstance(obj.get('String'), str):
            yield obj['String']
        if isinstance(obj.get('Number'), list):
            nums = ', '.join(str(x) for x in obj['Number'])
            unit = str(obj.get('Unit') or '').strip()
            if nums:
                yield (nums + (' ' + unit if unit else '')).strip()
        for value in obj.values():
            yield from _walk_pubchem_strings(value)
    elif isinstance(obj, list):
        for value in obj:
            yield from _walk_pubchem_strings(value)


def _density_value_g_ml(text: str):
    """Parse a density annotation and normalize supported units to g/mL."""
    t = str(text or '').replace('³', '3').replace('−', '-').replace('–', '-')
    patterns = [
        (r'(?<![0-9])([0-9]+(?:\.[0-9]+)?)\s*g\s*/\s*(?:mL|ml)\b', 1.0),
        (r'(?<![0-9])([0-9]+(?:\.[0-9]+)?)\s*g\s*/\s*cm3\b', 1.0),
        (r'(?<![0-9])([0-9]+(?:\.[0-9]+)?)\s*kg\s*/\s*L\b', 1.0),
        (r'(?<![0-9])([0-9]+(?:\.[0-9]+)?)\s*kg\s*/\s*m3\b', 0.001),
    ]
    for pattern, factor in patterns:
        m = re.search(pattern, t, flags=re.I)
        if m:
            value = float(m.group(1)) * factor
            if 0.2 <= value <= 5.0:  # broad sanity range for liquid perfumery materials
                return value
    return None


def _temperature_c(text: str):
    t = str(text or '').replace('°', '')
    m = re.search(r'(-?[0-9]+(?:\.[0-9]+)?)\s*(?:deg\s*)?C\b', t, flags=re.I)
    return float(m.group(1)) if m else None


def _extract_density_candidates(payload: dict) -> list[dict]:
    """Extract unique measured-density-looking values from PubChem PUG-View output."""
    rows=[]
    seen=set()
    for raw in _walk_pubchem_strings(payload):
        value=_density_value_g_ml(raw)
        if value is None:
            continue
        key=(round(value, 8), raw.strip())
        if key in seen:
            continue
        seen.add(key)
        temp=_temperature_c(raw)
        rows.append({'g_ml': value, 'temperature_c': temp, 'raw': raw.strip(), 'source': 'PubChem PUG-View'})
    # Prefer typical room-temperature measurements, then values that state a temperature.
    rows.sort(key=lambda x: (0 if x['temperature_c'] is not None and 15 <= x['temperature_c'] <= 30 else 1,
                             0 if x['temperature_c'] is not None else 1,
                             abs((x['temperature_c'] if x['temperature_c'] is not None else 25)-25)))
    return rows


def _preferred_density(candidates: list[dict]):
    """Return a conservative preferred density or None when annotations disagree materially."""
    if not candidates:
        return None
    preferred=[x for x in candidates if x['temperature_c'] is not None and 15 <= x['temperature_c'] <= 30]
    pool=preferred or candidates
    values=[float(x['g_ml']) for x in pool]
    if len(values) > 1:
        lo,hi=min(values),max(values)
        # Do not silently select among substantially conflicting annotations.
        if lo > 0 and (hi-lo)/lo > 0.08:
            return None
    return pool[0]

def fetch_pubchem_by_cas(cas_text: str, timeout: float = 10.0) -> dict:
    """Fetch a compact, cacheable PubChem identity record using official PUG REST."""
    last_error = None
    for cas in cas_tokens(cas_text):
        try:
            base = 'https://pubchem.ncbi.nlm.nih.gov/rest/pug'
            cids_payload = _get_json(f'{base}/compound/name/{quote(cas)}/cids/JSON', timeout)
            cids = cids_payload.get('IdentifierList', {}).get('CID', [])
            if not cids:
                continue
            cid = int(cids[0])
            prop_names = 'Title,IUPACName,MolecularFormula,MolecularWeight,CanonicalSMILES,IsomericSMILES,InChIKey'
            try:
                props_payload = _get_json(f'{base}/compound/cid/{cid}/property/{prop_names}/JSON', timeout)
            except Exception:
                prop_names = 'Title,IUPACName,MolecularFormula,MolecularWeight,ConnectivitySMILES,SMILES,InChIKey'
                props_payload = _get_json(f'{base}/compound/cid/{cid}/property/{prop_names}/JSON', timeout)
            props = (props_payload.get('PropertyTable', {}).get('Properties') or [{}])[0]
            try:
                syn_payload = _get_json(f'{base}/compound/cid/{cid}/synonyms/JSON', timeout)
                synonyms = (syn_payload.get('InformationList', {}).get('Information') or [{}])[0].get('Synonym', [])[:120]
            except Exception:
                synonyms = []
            try:
                desc_payload = _get_json(f'{base}/compound/cid/{cid}/description/JSON', timeout)
                info = (desc_payload.get('InformationList', {}).get('Information') or [{}])[0]
                description = info.get('Description') or ''
                if not props.get('Title'):
                    props['Title'] = info.get('Title') or ''
            except Exception:
                description = ''
            density_candidates = []
            try:
                density_payload = _get_json(
                    f'https://pubchem.ncbi.nlm.nih.gov/rest/pug_view/data/compound/{cid}/JSON?heading=Density',
                    timeout,
                )
                density_candidates = _extract_density_candidates(density_payload)
            except Exception:
                density_candidates = []
            preferred_density = _preferred_density(density_candidates)
            return {
                'cas': cas,
                'cid': cid,
                'title': props.get('Title') or '',
                'iupac_name': props.get('IUPACName') or '',
                'molecular_formula': props.get('MolecularFormula') or '',
                'molecular_weight': str(props.get('MolecularWeight') or ''),
                'canonical_smiles': props.get('CanonicalSMILES') or props.get('ConnectivitySMILES') or '',
                'isomeric_smiles': props.get('IsomericSMILES') or props.get('SMILES') or '',
                'inchikey': props.get('InChIKey') or '',
                'synonyms': synonyms,
                'description': description,
                'density_candidates': density_candidates,
                'preferred_density_g_ml': (preferred_density or {}).get('g_ml'),
                'preferred_density_raw': (preferred_density or {}).get('raw', ''),
                'fetched_at': datetime.now().isoformat(timespec='seconds'),
            }
        except Exception as exc:
            last_error = exc
    if last_error:
        raise last_error
    raise ValueError('No PubChem compound was found for the supplied CAS number.')



def _strip_html_text(text: str) -> str:
    text = re.sub(r'(?is)<script.*?</script>|<style.*?</style>', ' ', str(text or ''))
    text = re.sub(r'(?s)<[^>]+>', ' ', text)
    text = html_lib.unescape(text)
    return re.sub(r'\s+', ' ', text).strip()


def _parse_comptox_density_summary(text: str) -> dict | None:
    """Parse the Dashboard's Density summary row.

    The public Dashboard presents columns in this order: experimental average, predicted
    average, experimental median, predicted median, ranges, unit. We only need the first
    two values and deliberately label predicted data when no measured value is available.
    """
    plain = _strip_html_text(text)
    m = re.search(r'\bDensity\b\s+(-|[0-9.+\-Ee]+)(?:\s*\(\d+\))?\s+(-|[0-9.+\-Ee]+)(?:\s*\(\d+\))?.{0,220}?g\s*/\s*cm(?:\^?3|3)', plain, re.I)
    if not m:
        return None
    def num(v):
        if v == '-': return None
        try: return float(v)
        except Exception: return None
    exp, pred = num(m.group(1)), num(m.group(2))
    if exp is not None and 0.2 <= exp <= 5.0:
        return {'g_ml': exp, 'type': 'experimental', 'raw': m.group(0)[:500]}
    if pred is not None and 0.2 <= pred <= 5.0:
        return {'g_ml': pred, 'type': 'predicted', 'raw': m.group(0)[:500]}
    return None


def _ctx_headers() -> dict:
    headers={'User-Agent':'PerfumeStudio/0.9.10','Accept':'application/json'}
    key=os.environ.get('CTX_API_KEY') or os.environ.get('COMPTOX_API_KEY') or ''
    if key: headers['x-api-key']=key
    return headers


def _find_dtxsid_from_comptox(cas: str, pubchem_record: dict | None = None, timeout: float = 10.0) -> str:
    # PubChem commonly carries DSSTox DTXSID identifiers in its synonym set; use that
    # first because it avoids a second identity-resolution request.
    for value in (pubchem_record or {}).get('synonyms', []) or []:
        m=DTXSID_RE.search(str(value))
        if m: return m.group(0).upper()
    try:
        url=f'https://comptox.epa.gov/ctx-api/chemical/search/equal/{quote(cas)}'
        r=requests.get(url,timeout=timeout,headers=_ctx_headers())
        if r.status_code==200:
            payload=r.json()
            candidates=payload if isinstance(payload,list) else [payload]
            for row in candidates:
                if isinstance(row,dict):
                    for key in ('dtxsid','DTXSID'):
                        if row.get(key): return str(row[key]).upper()
            m=DTXSID_RE.search(r.text)
            if m:return m.group(0).upper()
    except Exception:
        pass
    return ''


def fetch_comptox_density_by_cas(cas_text: str, pubchem_record: dict | None = None, timeout: float = 10.0) -> dict:
    """Best-effort EPA CompTox density fallback.

    Experimental CTX API data are preferred when accessible. The CTX API may require an
    EPA API key; users can optionally expose CTX_API_KEY/COMPTOX_API_KEY. Without one, the
    public Chemicals Dashboard properties page is used as a read-only fallback. Predicted
    values are returned only when no experimental value is available and are explicitly
    marked as predicted.
    """
    last_error=None
    for cas in cas_tokens(cas_text):
        dtxsid=_find_dtxsid_from_comptox(cas,pubchem_record,timeout)
        if not dtxsid:
            continue
        # Official CTX Chemical API first.
        try:
            headers=_ctx_headers()
            url=f'https://comptox.epa.gov/ctx-api/chemical/property/experimental/search/by-dtxsid/{quote(dtxsid)}'
            r=requests.get(url,timeout=timeout,headers=headers)
            if r.status_code==200:
                payload=r.json()
                rows=payload if isinstance(payload,list) else payload.get('content',[]) if isinstance(payload,dict) else []
                vals=[]
                for row in rows:
                    if not isinstance(row,dict):continue
                    name=str(row.get('name') or row.get('propName') or row.get('propertyName') or '')
                    unit=str(row.get('unit') or row.get('propUnit') or '')
                    if name.strip().lower()!='density':continue
                    value=row.get('value',row.get('propValue',row.get('resultValue')))
                    try:v=float(value)
                    except Exception:continue
                    if unit.lower().replace(' ','') in ('g/cm^3','g/cm3','g/ml') and 0.2<=v<=5.0:
                        vals.append(v)
                if vals:
                    v=sum(vals)/len(vals)
                    return {'cas':cas,'dtxsid':dtxsid,'density_g_ml':v,'density_type':'experimental',
                            'raw':f'CompTox CTX experimental density; n={len(vals)}; values={", ".join(f"{x:g}" for x in vals[:12])} g/cm^3',
                            'fetched_at':datetime.now().isoformat(timespec='seconds')}
            elif r.status_code not in (401,403,404):
                r.raise_for_status()
        except Exception as exc:
            last_error=exc

        # Public Dashboard summary fallback (also exposes predicted values).
        try:
            url=f'https://comptox.epa.gov/dashboard/chemical/properties/{quote(dtxsid)}'
            r=requests.get(url,timeout=timeout,headers={'User-Agent':'PerfumeStudio/0.9.10'})
            r.raise_for_status()
            parsed=_parse_comptox_density_summary(r.text)
            if parsed:
                return {'cas':cas,'dtxsid':dtxsid,'density_g_ml':parsed['g_ml'],'density_type':parsed['type'],
                        'raw':'CompTox Dashboard: '+parsed['raw'],
                        'fetched_at':datetime.now().isoformat(timespec='seconds')}
        except Exception as exc:
            last_error=exc
    if last_error: raise last_error
    raise ValueError('No CompTox density was found for the supplied CAS number.')


def save_reference_snapshot(db, material_id: int, ifra: dict | None = None, pubchem: dict | None = None,
                            note_position: str | None = None, odor_notes: str | None = None, comptox: dict | None = None) -> None:
    current = db.query('SELECT * FROM material_details WHERE material_id=?', (material_id,))
    old = dict(current[0]) if current else {}
    pubchem = pubchem or {}
    comptox = comptox or {}
    ifra = ifra or {}
    values = {
        'material_id': material_id,
        'note_position': old.get('note_position', '') if note_position is None else note_position,
        'odor_notes': old.get('odor_notes', '') if odor_notes is None else odor_notes,
        'pubchem_cid': pubchem.get('cid', old.get('pubchem_cid')),
        'pubchem_title': pubchem.get('title', old.get('pubchem_title', '')),
        'pubchem_iupac_name': pubchem.get('iupac_name', old.get('pubchem_iupac_name', '')),
        'molecular_formula': pubchem.get('molecular_formula', old.get('molecular_formula', '')),
        'molecular_weight': pubchem.get('molecular_weight', old.get('molecular_weight', '')),
        'canonical_smiles': pubchem.get('canonical_smiles', old.get('canonical_smiles', '')),
        'isomeric_smiles': pubchem.get('isomeric_smiles', old.get('isomeric_smiles', '')),
        'inchikey': pubchem.get('inchikey', old.get('inchikey', '')),
        'pubchem_synonyms': json.dumps(pubchem.get('synonyms', json.loads(old.get('pubchem_synonyms') or '[]')), ensure_ascii=False),
        'pubchem_description': pubchem.get('description', old.get('pubchem_description', '')),
        'pubchem_density': pubchem.get('preferred_density_g_ml', old.get('pubchem_density')),
        'pubchem_density_raw': pubchem.get('preferred_density_raw', old.get('pubchem_density_raw', '')),
        'pubchem_fetched_at': pubchem.get('fetched_at', old.get('pubchem_fetched_at', '')),
        'comptox_dtxsid': comptox.get('dtxsid', old.get('comptox_dtxsid', '')),
        'comptox_density': comptox.get('density_g_ml', old.get('comptox_density')),
        'comptox_density_type': comptox.get('density_type', old.get('comptox_density_type', '')),
        'comptox_density_raw': comptox.get('raw', old.get('comptox_density_raw', '')),
        'comptox_fetched_at': comptox.get('fetched_at', old.get('comptox_fetched_at', '')),
        'ifra_summary': ifra.get('summary', old.get('ifra_summary', '')),
        'ifra_fetched_at': datetime.now().isoformat(timespec='seconds') if ifra else old.get('ifra_fetched_at', ''),
    }
    with db.connect() as conn:
        conn.execute(
            """INSERT INTO material_details(material_id,note_position,odor_notes,pubchem_cid,pubchem_title,pubchem_iupac_name,
                       molecular_formula,molecular_weight,canonical_smiles,isomeric_smiles,inchikey,pubchem_synonyms,
                       pubchem_description,pubchem_density,pubchem_density_raw,pubchem_fetched_at,comptox_dtxsid,comptox_density,comptox_density_type,comptox_density_raw,comptox_fetched_at,ifra_summary,ifra_fetched_at)
               VALUES(:material_id,:note_position,:odor_notes,:pubchem_cid,:pubchem_title,:pubchem_iupac_name,
                      :molecular_formula,:molecular_weight,:canonical_smiles,:isomeric_smiles,:inchikey,:pubchem_synonyms,
                      :pubchem_description,:pubchem_density,:pubchem_density_raw,:pubchem_fetched_at,:comptox_dtxsid,:comptox_density,:comptox_density_type,:comptox_density_raw,:comptox_fetched_at,:ifra_summary,:ifra_fetched_at)
               ON CONFLICT(material_id) DO UPDATE SET
                 note_position=excluded.note_position, odor_notes=excluded.odor_notes,
                 pubchem_cid=excluded.pubchem_cid, pubchem_title=excluded.pubchem_title,
                 pubchem_iupac_name=excluded.pubchem_iupac_name, molecular_formula=excluded.molecular_formula,
                 molecular_weight=excluded.molecular_weight, canonical_smiles=excluded.canonical_smiles,
                 isomeric_smiles=excluded.isomeric_smiles, inchikey=excluded.inchikey,
                 pubchem_synonyms=excluded.pubchem_synonyms, pubchem_description=excluded.pubchem_description,
                 pubchem_density=excluded.pubchem_density, pubchem_density_raw=excluded.pubchem_density_raw,
                 pubchem_fetched_at=excluded.pubchem_fetched_at, comptox_dtxsid=excluded.comptox_dtxsid,
                 comptox_density=excluded.comptox_density, comptox_density_type=excluded.comptox_density_type,
                 comptox_density_raw=excluded.comptox_density_raw, comptox_fetched_at=excluded.comptox_fetched_at, ifra_summary=excluded.ifra_summary,
                 ifra_fetched_at=excluded.ifra_fetched_at""",
            values,
        )
