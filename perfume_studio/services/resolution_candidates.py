from __future__ import annotations

"""General material-identity candidate resolver.

Nothing in this module contains material-specific answers.  Candidate generation is based on:
  1) names already known locally (user aliases, bundled aliases, IFRA Standards/NCS, cached Transparency rows)
  2) generic normalization/token/fuzzy scoring
  3) PubChem autocomplete + synonyms as an automatic fallback, cached locally

PubChem is never treated as an automatic authority.  Online hits are suggestions that the user
chooses in Resolve Enrich Error.  Once chosen, the normal material_aliases table remembers it.
"""

from dataclasses import dataclass
from difflib import SequenceMatcher
import json
import math
import re
import time
from urllib.parse import quote

from perfume_studio.services.inventory_enrichment import normalize_material_name, material_name_candidates
from perfume_studio.services.ifra_engine import split_cas

CAS_RE = re.compile(r"\b\d{2,7}-\d{2}-\d\b")

# Generic product/form words. Removing these for one of the search variants is not an identity
# claim; it simply lets "Heliotrope Base" search the chemical-word stem "heliotrope".
GENERIC_QUERY_WORDS = {
    'base','accord','blend','mixture','oil','essential','absolute','resinoid','resin','tincture',
    'extract','co2','signature','superior','dark','light','pure','synthetic','natural','fcf',
    'sicilian','technical','grade','undiluted','neat','solution','dilution','pa','type','quality',
}


def _query_variants(name: str) -> list[str]:
    out = list(material_name_candidates(name))
    raw = normalize_material_name(name)
    if raw:
        tokens = raw.split()
        stripped = ' '.join(t for t in tokens if t not in GENERIC_QUERY_WORDS and not re.fullmatch(r'\d+', t))
        if stripped and stripped not in out:
            out.append(stripped)
        # Prefix chunks are useful for fuzzy dictionary lookups but are never auto-applied.
        if stripped:
            for token in stripped.split():
                if len(token) >= 5 and token not in out:
                    out.append(token)
    return [x for x in out if x]


def _token_score(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    seq = SequenceMatcher(None, a, b).ratio()
    at, bt = set(a.split()), set(b.split())
    jac = len(at & bt) / max(1, len(at | bt))
    contain = 0.0
    if a in b or b in a:
        contain = 0.90
    # Compare individual long-token stems so e.g. heliotrope/heliotropin can surface.
    stem = 0.0
    for x in at:
        if len(x) < 5:
            continue
        for y in bt:
            if len(y) < 5:
                continue
            stem = max(stem, SequenceMatcher(None, x, y).ratio())
    return max(seq * 0.72 + jac * 0.28, contain, stem * 0.91)


def _longest_common_prefix(a: str, b: str) -> int:
    n = 0
    for x, y in zip(a, b):
        if x != y:
            break
        n += 1
    return n


def _lexically_related(needles: list[str], *names: str) -> bool:
    """Conservative name-relevance gate for fuzzy/autocomplete candidates.

    This is intentionally different from ranking. A result can have a non-trivial edit-distance
    score merely because two fragrance names share a common suffix (for example *-one*). Such a
    result must not enter the dropdown unless there is a more meaningful lexical relationship.

    Accepted relationships include:
      * an exact normalized name;
      * a complete long token from the query appearing as a token in the candidate (or vice versa);
      * a strong shared prefix of at least five characters, which keeps useful cases such as
        heliotrope -> heliotropin while rejecting muscenone -> ionone gamma / damascenone.
    Exact trade-name -> unrelated chemical-name bridges still come from explicit aliases or an
    exact PubChem synonym lookup, not from this fuzzy gate.
    """
    candidate_names = [normalize_material_name(x) for x in names if normalize_material_name(x)]
    for needle in needles:
        n = normalize_material_name(needle)
        if not n:
            continue
        nt = [t for t in n.split() if len(t) >= 4]
        for c in candidate_names:
            if n == c:
                return True
            ct = [t for t in c.split() if len(t) >= 4]
            # Whole-token containment: muscenone -> muscenone delta / 5 muscenone.
            if any(x == y for x in nt for y in ct):
                return True
            # Strong prefix morphology: heliotrope -> heliotropin. Requiring five common
            # leading characters prevents suffix-only coincidences such as ionone/damascenone.
            for x in nt:
                for y in ct:
                    lcp = _longest_common_prefix(x, y)
                    shorter = min(len(x), len(y))
                    if lcp >= 5 and shorter and (lcp / shorter) >= 0.62:
                        return True
    return False


def _best_score(needles: list[str], *names: str) -> float:
    best = 0.0
    for needle in needles:
        for name in names:
            n = normalize_material_name(name)
            if n:
                best = max(best, _token_score(needle, n))
    return best


def _add_candidate(bucket: list[dict], needles: list[str], display_name: str, principal_name: str,
                   cas: str, source: str, notes: str = '', confidence: float = 1.0,
                   score_boost: float = 0.0):
    if not cas or not (display_name or principal_name):
        return
    score = _best_score(needles, display_name, principal_name)
    score = min(1.0, score + score_boost)
    if score < 0.60 or not _lexically_related(needles, display_name, principal_name):
        return
    bucket.append({
        'display_name': display_name or principal_name,
        'principal_name': principal_name or display_name,
        'cas': cas,
        'source': source,
        'notes': notes or '',
        'confidence': float(confidence or 1.0),
        'score': score,
        'match_type': 'algorithmic candidate',
    })


def _local_candidates(db, name: str) -> list[dict]:
    needles = _query_variants(name)
    if not needles:
        return []
    out: list[dict] = []

    for row in db.query('SELECT alias, principal_name, cas, source, confidence, notes FROM material_aliases WHERE TRIM(cas)<>\'\''):
        _add_candidate(out, needles, row['alias'], row['principal_name'] or row['alias'], row['cas'],
                       row['source'], row['notes'] or '', row['confidence'] or 1.0,
                       0.03 if str(row['source']).startswith('User') else 0.0)

    for row in db.query('SELECT name, cas_numbers, synonyms FROM ifra_standards WHERE TRIM(COALESCE(cas_numbers,\'\'))<>\'\''):
        cases = split_cas(row['cas_numbers'])
        if not cases:
            continue
        cas = '; '.join(dict.fromkeys(cases))
        _add_candidate(out, needles, row['name'], row['name'], cas, 'IFRA 51 Standard')
        for syn in re.split(r'[\n;|]+', row['synonyms'] or ''):
            if syn.strip():
                _add_candidate(out, needles, syn.strip(), row['name'], cas, 'IFRA 51 synonym')

    seen_ncs = set()
    for row in db.query('SELECT ncs_name, botanical_name, principal_cas, other_cas FROM ncs_contributions'):
        cases = list(dict.fromkeys(split_cas(row['principal_cas']) + split_cas(row['other_cas'])))
        if not cases:
            continue
        cas = '; '.join(cases)
        key = (row['ncs_name'], cas)
        if key in seen_ncs:
            continue
        seen_ncs.add(key)
        _add_candidate(out, needles, row['ncs_name'], row['ncs_name'], cas, 'IFRA 51 NCS Annex')
        if row['botanical_name']:
            _add_candidate(out, needles, row['botanical_name'], row['ncs_name'], cas, 'IFRA 51 NCS botanical name')

    for row in db.query('SELECT principal_name, cas, ncs_category FROM transparency_materials'):
        _add_candidate(out, needles, row['principal_name'], row['principal_name'], row['cas'],
                       'IFRA Transparency local cache', row['ncs_category'] or '', 1.0, 0.02)
    return out


def _cached_pubchem(db, normalized_query: str) -> list[dict] | None:
    rows = db.query('SELECT payload_json FROM identity_search_cache WHERE provider=? AND normalized_query=? LIMIT 1',
                    ('PubChem', normalized_query))
    if not rows:
        return None
    try:
        data = json.loads(rows[0]['payload_json'])
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _save_pubchem_cache(db, normalized_query: str, rows: list[dict]):
    with db.connect() as conn:
        conn.execute('''INSERT INTO identity_search_cache(provider, normalized_query, payload_json, fetched_at)
                        VALUES(?,?,?,CURRENT_TIMESTAMP)
                        ON CONFLICT(provider, normalized_query) DO UPDATE SET
                          payload_json=excluded.payload_json, fetched_at=CURRENT_TIMESTAMP''',
                     ('PubChem', normalized_query, json.dumps(rows, ensure_ascii=False)))


def _pubchem_http_get(url: str, timeout: float = 5.0):
    try:
        import requests
    except Exception:
        return None
    try:
        r = requests.get(url, timeout=timeout, headers={'User-Agent': 'PerfumeStudio/0.8.2 identity resolver'})
        if r.status_code != 200:
            return None
        return r.json()
    except Exception:
        return None


def _pubchem_lookup_name(candidate_name: str) -> tuple[str, str] | None:
    """Return (PubChem title/name, a CAS-looking deposited synonym) or None."""
    encoded = quote(candidate_name, safe='')
    url = f'https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/name/{encoded}/synonyms/JSON'
    data = _pubchem_http_get(url)
    if not data:
        return None
    try:
        info = data['InformationList']['Information'][0]
        title = info.get('Title') or candidate_name
        syns = info.get('Synonym') or []
    except Exception:
        return None
    cases = []
    for syn in syns:
        m = CAS_RE.fullmatch(str(syn).strip())
        if m:
            cases.append(m.group(0))
    if not cases:
        return None
    # PubChem synonym lists can contain multiple registry-looking identifiers. We expose the
    # first unique one as a suggestion only; the resolver UI always requires user confirmation.
    return title, list(dict.fromkeys(cases))[0]


def _pubchem_lookup_cas(cas: str) -> list[dict]:
    """Resolve a user-supplied CAS to PubChem identity suggestions.

    This is only a fallback for the resolver's explicit *Search by CAS* action.  The CAS is
    retained exactly as typed after validation; PubChem supplies a human-readable identity name.
    """
    cas = (cas or '').strip()
    if not CAS_RE.fullmatch(cas):
        return []
    encoded = quote(cas, safe='')
    data = _pubchem_http_get(
        f'https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/name/{encoded}/property/Title,IUPACName/JSON'
    )
    if not data:
        return []
    rows = []
    try:
        props = data.get('PropertyTable', {}).get('Properties', [])
    except Exception:
        props = []
    for prop in props[:6]:
        title = (prop.get('Title') or prop.get('IUPACName') or cas).strip()
        principal = (prop.get('IUPACName') or title).strip()
        rows.append({
            'display_name': title,
            'principal_name': principal,
            'cas': cas,
            'source': 'PubChem CAS lookup',
            'notes': 'Identity name returned for the CAS you explicitly searched. User confirmation required.',
            'confidence': 0.75,
            'score': 1.0,
            'match_type': 'CAS lookup candidate',
        })
    return rows


def _cas_matches(field: str | None, cas: str) -> bool:
    return cas in set(split_cas(field or ''))


def search_resolution_candidates_by_cas(db, cas: str, limit: int = 12, allow_online: bool = True) -> list[dict]:
    """Search every local identity source by an exact CAS token.

    Unlike name ranking, CAS search is exact.  It is intended as the final user-driven fallback
    when a material name is too ambiguous or proprietary to resolve reliably.
    """
    cas = (cas or '').strip()
    if not CAS_RE.fullmatch(cas):
        return []
    out: list[dict] = []

    def add(display_name, principal_name, source, notes=''):
        display_name = (display_name or principal_name or cas).strip()
        principal_name = (principal_name or display_name).strip()
        out.append({
            'display_name': display_name,
            'principal_name': principal_name,
            'cas': cas,
            'source': source,
            'notes': notes or '',
            'confidence': 1.0,
            'score': 1.0,
            'match_type': 'exact CAS candidate',
        })

    for row in db.query("SELECT alias, principal_name, cas, source, notes FROM material_aliases WHERE TRIM(cas)<>''"):
        if _cas_matches(row['cas'], cas):
            add(row['alias'], row['principal_name'] or row['alias'], row['source'], row['notes'] or '')

    for row in db.query("SELECT name, cas_numbers, synonyms FROM ifra_standards WHERE TRIM(COALESCE(cas_numbers,''))<>''"):
        if not _cas_matches(row['cas_numbers'], cas):
            continue
        add(row['name'], row['name'], 'IFRA 51 Standard')
        for syn in re.split(r'[\n;|]+', row['synonyms'] or ''):
            if syn.strip():
                add(syn.strip(), row['name'], 'IFRA 51 synonym')

    seen_ncs = set()
    for row in db.query('SELECT ncs_name, botanical_name, principal_cas, other_cas FROM ncs_contributions'):
        if not (_cas_matches(row['principal_cas'], cas) or _cas_matches(row['other_cas'], cas)):
            continue
        key = (row['ncs_name'], cas)
        if key in seen_ncs:
            continue
        seen_ncs.add(key)
        add(row['ncs_name'], row['ncs_name'], 'IFRA 51 NCS Annex')
        if row['botanical_name']:
            add(row['botanical_name'], row['ncs_name'], 'IFRA 51 NCS botanical name')

    for row in db.query('SELECT principal_name, cas, ncs_category FROM transparency_materials'):
        if _cas_matches(row['cas'], cas):
            add(row['principal_name'], row['principal_name'], 'IFRA Transparency local cache', row['ncs_category'] or '')

    # Existing inventory rows are useful when the user already entered this CAS elsewhere.
    for row in db.query("SELECT name, cas FROM materials WHERE TRIM(COALESCE(cas,''))<>''"):
        if _cas_matches(row['cas'], cas):
            add(row['name'], row['name'], 'Existing Inventory')

    dedup = []
    seen = set()
    for item in out:
        key = (normalize_material_name(item['display_name']), normalize_material_name(item['principal_name']), item['cas'])
        if key in seen:
            continue
        seen.add(key)
        dedup.append(item)
        if len(dedup) >= max(1, int(limit)):
            break

    if not dedup and allow_online:
        dedup.extend(_pubchem_lookup_cas(cas)[:max(1, int(limit))])
    return dedup


def _pubchem_candidates(db, name: str, limit: int = 50) -> list[dict]:
    qnorm = normalize_material_name(name)
    variants = _query_variants(name)
    cached = _cached_pubchem(db, qnorm)
    if cached is not None:
        # Old application versions cached raw autocomplete suggestions, including weak suffix-only
        # lookalikes. Re-apply the current relevance gate every time cached data is read so an
        # upgrade fixes existing user databases without asking the user to clear a cache manually.
        filtered = []
        for row in cached:
            match_type = str(row.get('match_type') or '')
            if match_type == 'online exact-name candidate' or _lexically_related(
                    variants, row.get('display_name') or '', row.get('principal_name') or ''):
                filtered.append(row)
        filtered.sort(key=lambda x: (-float(x.get('score') or 0), -float(x.get('confidence') or 0),
                                     normalize_material_name(x.get('display_name') or '')))
        return filtered[:max(1, int(limit))]

    autocomplete_names: list[str] = []

    # At most two autocomplete calls to stay fast and respectful of PubChem's service limits.
    for query in variants[:2]:
        encoded = quote(query, safe='')
        data = _pubchem_http_get(
            f'https://pubchem.ncbi.nlm.nih.gov/rest/autocomplete/compound/{encoded}/json?limit=50'
        )
        if not data:
            continue
        terms = ((data.get('dictionary_terms') or {}).get('compound') or [])
        for term in terms:
            if term not in autocomplete_names:
                autocomplete_names.append(term)

    # If autocomplete returned nothing, exact/word PUG lookup can still resolve a trade name.
    if not autocomplete_names:
        autocomplete_names = [name]

    scored_names = sorted(
        ((max(_best_score(variants, term), 0.0), term) for term in autocomplete_names),
        key=lambda x: (-x[0], normalize_material_name(x[1]))
    )
    rows: list[dict] = []
    for score, term in scored_names[:max(limit, 4)]:
        # Autocomplete is allowed to return many nearby dictionary words. Only keep terms with a
        # meaningful lexical relationship to the user's query; low-score suffix coincidences must
        # never be used merely to fill the dropdown. Exact-name PubChem hits above bypass this gate.
        if not _lexically_related(variants, term):
            continue
        # PubChem's first job is name discovery. If that discovered/canonical name maps strongly
        # to an IFRA/local identity, prefer the CAS from that local identity rather than treating a
        # deposited PubChem synonym as the authority. This is the trade-name -> canonical-name ->
        # IFRA/local-identity path used by the resolver.
        local_for_term = sorted(_local_candidates(db, term), key=lambda x: -x['score'])
        if local_for_term and local_for_term[0]['score'] >= 0.965:
            hit = dict(local_for_term[0])
            hit.update({
                'display_name': term,
                'source': 'PubChem name discovery → ' + hit['source'],
                'notes': ('PubChem suggested this identity name; its CAS comes from the matching '
                          'local IFRA/reference identity. User confirmation required.'),
                'confidence': min(0.92, max(0.80, float(hit.get('confidence') or 0.8))),
                'score': max(score, _best_score(variants, term, hit['principal_name'])),
                'match_type': 'online name → local identity candidate',
            })
            rows.append(hit)
            continue

        identity = _pubchem_lookup_name(term)
        if not identity:
            continue
        title, cas = identity
        rows.append({
            'display_name': term,
            'principal_name': title,
            'cas': cas,
            'source': 'PubChem automatic fallback candidate',
            'notes': ('No strong local IFRA/reference identity matched the PubChem-discovered name; '
                      'this CAS is a PubChem synonym fallback and requires user confirmation.'),
            'confidence': 0.65,
            'score': max(score, _best_score(variants, title)),
            'match_type': 'online fuzzy fallback candidate',
        })
        # Keep below PubChem's published 5 req/s ceiling even on a fast connection.
        time.sleep(0.22)

    # If autocomplete did not produce a relevant candidate, try the exact inventory name once.
    # This preserves trade-name -> chemical-name synonym resolution without allowing unrelated
    # autocomplete fillers into the list.
    if not rows:
        identity = _pubchem_lookup_name(name)
        if identity:
            title, cas = identity
            rows.append({
                'display_name': name,
                'principal_name': title,
                'cas': cas,
                'source': 'PubChem exact synonym lookup',
                'notes': ('PubChem resolved the exact inventory name as a deposited synonym. '
                          'User confirmation is still required.'),
                'confidence': 0.78,
                'score': 1.0,
                'match_type': 'online exact-name candidate',
            })
    _save_pubchem_cache(db, qnorm, rows)
    return rows[:max(1, int(limit))]


def get_resolution_candidates(db, name: str, limit: int = 50, allow_online: bool = True) -> list[dict]:
    """Return top algorithmic candidates. No material-specific mapping table is consulted."""
    results = _local_candidates(db, name)
    results.sort(key=lambda x: (-x['score'], normalize_material_name(x['display_name'])))

    # If local data does not confidently explain the name, automatically ask PubChem for name
    # candidates. The user never has to open/search PubChem manually; results are cached.
    strong_local = [r for r in results if r.get('score', 0) >= 0.88]
    if allow_online and (not results or (results[0]['score'] < 0.92 and len(strong_local) < 2)):
        results.extend(_pubchem_candidates(db, name, limit=max(20, limit)))

    results.sort(key=lambda x: (-x['score'], -x.get('confidence', 0), normalize_material_name(x['display_name'])))
    dedup: list[dict] = []
    seen = set()
    for item in results:
        key = (normalize_material_name(item['principal_name']), item['cas'])
        if key in seen:
            continue
        seen.add(key)
        dedup.append(item)
        if len(dedup) >= max(1, int(limit)):
            break
    return dedup


def high_confidence_local_candidate(db, name: str) -> dict | None:
    """Safe automatic resolution: only very strong local matches with a clear margin."""
    rows = _local_candidates(db, name)
    if not rows:
        return None
    rows.sort(key=lambda x: -x['score'])
    top = rows[0]
    second = rows[1]['score'] if len(rows) > 1 else 0.0
    if top['score'] >= 0.985 and top['score'] - second >= 0.025:
        return top
    return None
