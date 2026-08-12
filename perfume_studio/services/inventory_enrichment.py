from __future__ import annotations

from dataclasses import dataclass
from difflib import SequenceMatcher
import re

from perfume_studio.services.ifra_engine import split_cas


PREDILUTION_RE = re.compile(
    r"(?ix)"
    r"(?:\s*[\(\[\{]?\s*)"
    r"(?:@\s*)?"
    r"(?P<pct>\d+(?:[.,]\d+)?)\s*(?:%|pct\.?|percent)"
    r"(?:\s*(?:in\s+)?(?P<solvent>DPG|TEC|IPM|EtOH|Ethanol|Alcohol|PG))?"
    r"\s*[\)\]\}]?\s*$"
)

CAS_TOKEN_RE = re.compile(r"\b\d{2,7}-\d{2}-\d\b")
LEADING_BULLET_RE = re.compile(r"^\s*(?:[-*•·]+|\d+[.)])\s+")


def normalize_material_name(value: str | None) -> str:
    s = (value or '').strip().lower()
    s = s.replace('™', '').replace('®', '').replace('©', '')
    s = s.replace('&', ' and ')
    s = re.sub(r"\b(?:solution|dilution)\b", ' ', s)
    s = re.sub(r"[^a-z0-9]+", ' ', s)
    return ' '.join(s.split())


LOOKUP_QUALIFIERS = {
    'undiluted', 'neat', 'synthetic', 'natural', 'pure', 'technical', 'grade'
}


def material_name_candidates(value: str | None) -> list[str]:
    """Return conservative lookup aliases while preserving the user's display name.

    This is intentionally only used for matching. It does not rewrite the inventory cell.
    It handles common perfumery list formats such as:
      Evernyl (Veramoss) -> Evernyl, Veramoss
      Florol (Pyranol, Florosa) -> Florol, Pyranol, Florosa
      Alpha Isomethyl Ionone / Ionone Gamma -> both sides
      Linalool Synthetic -> Linalool
      Galaxolide 100 (Undiluted) -> Galaxolide 100, Galaxolide
    """
    raw = (value or '').strip()
    if not raw:
        return []

    out: list[str] = []

    def add(text: str):
        n = normalize_material_name(text)
        if n and n not in out:
            out.append(n)

    add(raw)

    # Parenthetical text is often a trade-name alias, but descriptors such as
    # "Undiluted" should simply be ignored for lookup.
    parens = re.findall(r"\(([^()]*)\)", raw)
    without_parens = re.sub(r"\([^()]*\)", ' ', raw)
    add(without_parens)
    for group in parens:
        for alias in re.split(r"[,;/|]+", group):
            if normalize_material_name(alias) not in LOOKUP_QUALIFIERS:
                add(alias)

    # Slash/comma separated aliases in the main name.
    for alias in re.split(r"\s*/\s*|\s*\|\s*", without_parens):
        add(alias)

    # Remove generic descriptors only for lookup.
    words = normalize_material_name(without_parens).split()
    trimmed = [w for w in words if w not in LOOKUP_QUALIFIERS]
    if trimmed:
        add(' '.join(trimmed))

    # "Galaxolide 100" / similar supplier nomenclature: try the base name too,
    # but only remove a terminal 100 so arbitrary product numbers are not stripped.
    no_100 = re.sub(r"\s+100\s*$", '', ' '.join(trimmed)).strip()
    if no_100:
        add(no_100)

    return out


@dataclass
class ParsedInventoryLine:
    name: str
    predilution_pct: float = 100.0
    solvent: str = ''
    cas: str = ''


def parse_inventory_line(text: str) -> ParsedInventoryLine:
    """Parse common pasted inventory names without inventing metadata.

    Examples:
      Evernyl 10% -> name=Evernyl, predilution=10
      Ambroxan (20% DPG) -> name=Ambroxan, predilution=20, solvent=DPG
      1. Hedione -> name=Hedione, predilution=100
    """
    raw = LEADING_BULLET_RE.sub('', (text or '').strip())
    cas_match = CAS_TOKEN_RE.search(raw)
    cas = cas_match.group(0) if cas_match else ''
    if cas_match:
        raw = (raw[:cas_match.start()] + ' ' + raw[cas_match.end():]).strip(' -|,;')

    pred = 100.0
    solvent = ''
    m = PREDILUTION_RE.search(raw)
    if m:
        try:
            pred = float(m.group('pct').replace(',', '.'))
        except Exception:
            pred = 100.0
        solvent = (m.group('solvent') or '').strip()
        raw = raw[:m.start()].strip(' -|,;([{')

    # Also accept "Material - 10%" because supplier lists often use separators.
    if pred == 100.0:
        m = re.search(r"(?ix)\s*[-|,;]\s*(\d+(?:[.,]\d+)?)\s*%\s*$", raw)
        if m:
            try:
                pred = float(m.group(1).replace(',', '.'))
                raw = raw[:m.start()].strip(' -|,;')
            except Exception:
                pass

    return ParsedInventoryLine(name=raw.strip(), predilution_pct=pred, solvent=solvent, cas=cas)


def _aliases(text: str | None) -> list[str]:
    if not text:
        return []
    # IFRA synonym cells are not guaranteed to use one delimiter, so split conservatively.
    chunks = re.split(r"[\n;|]+", str(text))
    return [c.strip() for c in chunks if c and c.strip()]


def build_ifra_name_index(db):
    """Build a conservative alias -> CAS index from already-imported official IFRA data.

    Exact normalized matches are preferred. Fuzzy matching is only exposed separately and
    uses a high threshold so a CAS is never fabricated from a weak similarity.
    """
    index: dict[str, list[dict]] = {}

    def add(alias: str, cas: str, source: str, canonical: str):
        n = normalize_material_name(alias)
        if not n or not cas:
            return
        index.setdefault(n, []).append({
            'alias': alias,
            'cas': cas,
            'source': source,
            'canonical': canonical,
        })

    for row in db.query('SELECT name, cas_numbers, synonyms FROM ifra_standards'):
        cases = split_cas(row['cas_numbers'])
        if not cases:
            continue
        cas = '; '.join(dict.fromkeys(cases))
        add(row['name'], cas, 'IFRA Standard', row['name'])
        for alias in _aliases(row['synonyms']):
            add(alias, cas, 'IFRA synonym', row['name'])

    # NCS annex is useful for naturals that are not themselves restricted Standards.
    seen = set()
    for row in db.query('SELECT ncs_name, botanical_name, principal_cas, other_cas FROM ncs_contributions'):
        principal = split_cas(row['principal_cas'])
        other = split_cas(row['other_cas'])
        cases = list(dict.fromkeys(principal + other))
        if not cases:
            continue
        cas = '; '.join(cases)
        key = (row['ncs_name'], cas)
        if key in seen:
            continue
        seen.add(key)
        add(row['ncs_name'], cas, 'IFRA NCS Annex', row['ncs_name'])
        if row['botanical_name']:
            add(row['botanical_name'], cas, 'IFRA NCS botanical name', row['ncs_name'])
    return index


def enrich_name_from_ifra(name: str, index: dict[str, list[dict]]):
    """Return a high-confidence IFRA/NCS match or None.

    Ambiguous exact aliases are rejected. Fuzzy matching is deliberately strict (>= .96)
    and must have a clear margin over the runner-up.
    """
    needles = material_name_candidates(name)
    if not needles:
        return None

    for needle in needles:
        exact = index.get(needle, [])
        exact_cases = {x['cas'] for x in exact}
        if len(exact_cases) == 1 and exact:
            best = exact[0].copy()
            best['score'] = 1.0
            best['match_type'] = 'exact'
            best['matched_candidate'] = needle
            return best
        if len(exact_cases) > 1:
            # Ambiguous aliases are deliberately not auto-filled.
            continue

    # A deliberately conservative fuzzy fallback catches punctuation/spelling variants,
    # while leaving uncertain cells blank for manual review.
    scored = []
    for needle in needles:
        for alias, entries in index.items():
            if not alias:
                continue
            score = SequenceMatcher(None, needle, alias).ratio()
            if score >= 0.96:
                scored.append((score, alias, entries, needle))
    if not scored:
        return None
    scored.sort(key=lambda x: x[0], reverse=True)
    best_score, _, entries, needle = scored[0]
    # De-duplicate repeated hits that point to the same alias/CAS before applying
    # the runner-up margin check.
    distinct = []
    seen = set()
    for row in scored:
        key = (row[1], tuple(sorted(x['cas'] for x in row[2])))
        if key not in seen:
            seen.add(key)
            distinct.append(row)
    if len(distinct) > 1 and best_score - distinct[1][0] < 0.025:
        return None
    cases = {x['cas'] for x in entries}
    if len(cases) != 1:
        return None
    best = entries[0].copy()
    best['score'] = best_score
    best['match_type'] = 'fuzzy'
    best['matched_candidate'] = needle
    return best
