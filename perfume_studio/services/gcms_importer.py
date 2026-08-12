from __future__ import annotations
import csv, re, difflib
from pathlib import Path
from dataclasses import dataclass
from typing import Iterable
import openpyxl

PERCENT_GENERIC_HEADERS = {'percentage','percent','%','content %','content%','area %','area%'}
PERCENT_REL_HEADERS = {'%rel','% rel','rel %','relative %','relative percent','relative percentage'}
PERCENT_ABS_HEADERS = {'%abs','% abs','abs %','absolute %','absolute percent','absolute percentage'}
NAME_HEADERS = {'formula entry','compound','compound name','material','material name','name','analyte','component','ingredient','formula'}
CAS_HEADERS = {'cas','cas no','cas no.','cas number','cas #'}
WEIGHT_HEADERS = {'weight','weight g','weight (g)','g','amount','amount g'}
CAS_RE = re.compile(r'\b\d{2,7}-\d{2}-\d\b')
PCT_RE = re.compile(r'(?<![\d.])(\d+(?:\.\d+)?)\s*%')

@dataclass
class GCMSRow:
    name: str
    percent: float
    cas: str = ''
    source: str = ''
    matched_material_id: int | None = None
    matched_material_name: str = ''
    score: float = 0.0
    percent_rel: float | None = None
    percent_abs: float | None = None
    weight: float | None = None
    predilution_pct: float | None = None


def _norm(s):
    return re.sub(r'[^a-z0-9%]+', ' ', str(s).lower()).strip()


def _find_col(headers, candidates):
    normalized = [_norm(x) for x in headers]
    cand = {_norm(c) for c in candidates}
    for i,h in enumerate(normalized):
        if h in cand:
            return i
    for i,h in enumerate(normalized):
        for c in cand:
            if c and c in h:
                return i
    return None


def _float_cell(value):
    if value is None:
        return None
    s=str(value).replace('%','').strip()
    if not s:
        return None
    if ',' in s and '.' not in s:
        s=s.replace(',','.')
    else:
        s=s.replace(',','')
    try:return float(s)
    except Exception:return None


def _rows_from_matrix(matrix: list[list], source: str):
    """Parse table data, preserving the old calculator's %Rel/%Abs distinction.

    The previous PerfumeCalculator worked with tables shaped like:
    Formula entry | Weight (g) | % Rel | % Abs.
    For PerfumeStudio we prefer %Abs as the formula part when present and fall back to
    %Rel/generic percentage only when needed.
    """
    if not matrix:
        return []
    header_idx = None
    name_idx = cas_idx = rel_idx = abs_idx = generic_idx = weight_idx = None
    for i,row in enumerate(matrix[:40]):
        vals = ['' if x is None else str(x) for x in row]
        n = _find_col(vals, NAME_HEADERS)
        rel = _find_col(vals, PERCENT_REL_HEADERS)
        abs_ = _find_col(vals, PERCENT_ABS_HEADERS)
        generic = _find_col(vals, PERCENT_GENERIC_HEADERS)
        weight = _find_col(vals, WEIGHT_HEADERS)
        if n is not None and any(x is not None for x in (rel, abs_, generic, weight)):
            header_idx, name_idx, rel_idx, abs_idx, generic_idx, weight_idx = i,n,rel,abs_,generic,weight
            cas_idx = _find_col(vals, CAS_HEADERS)
            break
    if header_idx is None:
        return []
    out=[]
    max_idx=max(x for x in (name_idx,cas_idx,rel_idx,abs_idx,generic_idx,weight_idx) if x is not None)
    for row in matrix[header_idx+1:]:
        if max_idx >= len(row):
            continue
        name = str(row[name_idx] or '').strip()
        if not name:
            continue
        rel=_float_cell(row[rel_idx]) if rel_idx is not None else None
        abs_=_float_cell(row[abs_idx]) if abs_idx is not None else None
        generic=_float_cell(row[generic_idx]) if generic_idx is not None else None
        weight=_float_cell(row[weight_idx]) if weight_idx is not None else None
        pct=abs_ if abs_ is not None else (rel if rel is not None else (generic if generic is not None else weight))
        if pct is None: continue
        cas = str(row[cas_idx] or '').strip() if cas_idx is not None else ''
        m = CAS_RE.search(cas)
        cas = m.group(0) if m else cas
        out.append(GCMSRow(name=name, percent=float(pct), cas=cas, source=source,
                           percent_rel=rel, percent_abs=abs_, weight=weight))
    return out


def import_csv(path: str | Path):
    path=Path(path)
    text=path.read_text(encoding='utf-8-sig', errors='replace')
    try:
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=',\t;|')
    except Exception:
        dialect=csv.excel_tab if '\t' in text[:4096] else csv.excel
    matrix=list(csv.reader(text.splitlines(), dialect))
    return _rows_from_matrix(matrix, path.name)


def import_xlsx(path: str | Path):
    wb=openpyxl.load_workbook(path, data_only=True, read_only=True)
    best=[]
    for ws in wb.worksheets:
        matrix=[list(r) for r in ws.iter_rows(values_only=True)]
        rows=_rows_from_matrix(matrix, f'{Path(path).name}:{ws.title}')
        if len(rows)>len(best): best=rows
    return best


def _ocr_dicts_to_rows(items):
    out=[]
    for x in items:
        rel=x.get('percent_rel'); abs_=x.get('percent_abs'); weight=x.get('weight')
        pct=abs_ if abs_ is not None else (rel if rel is not None else weight)
        if pct is None: continue
        out.append(GCMSRow(name=x['name'],percent=float(pct),cas=x.get('cas',''),source=x.get('source','OCR'),
                           percent_rel=rel,percent_abs=abs_,weight=weight,predilution_pct=x.get('predilution_pct')))
    return out


def import_image(path: str | Path):
    from perfume_studio.services.legacy_gcms_ocr import rows_from_image_path
    return _ocr_dicts_to_rows(rows_from_image_path(path))


def import_clipboard_image():
    from perfume_studio.services.legacy_gcms_ocr import rows_from_clipboard
    return _ocr_dicts_to_rows(rows_from_clipboard())


def import_pdf(path: str | Path):
    path=Path(path)
    rows=[]
    # Prefer structured text/tables; OCR is only the fallback for scanned pages.
    try:
        import pdfplumber
        with pdfplumber.open(path) as pdf:
            for pageno,page in enumerate(pdf.pages,1):
                for table in page.extract_tables() or []:
                    got=_rows_from_matrix(table, f'{path.name}:p{pageno}')
                    rows.extend(got)
        if rows:
            return rows
    except Exception:
        pass
    try:
        from pypdf import PdfReader
        reader=PdfReader(path)
        text_rows=[]
        for pageno,page in enumerate(reader.pages,1):
            txt=page.extract_text() or ''
            for line in txt.splitlines():
                # Text PDFs with explicit percentages remain supported.
                pcts=list(PCT_RE.finditer(line))
                if not pcts: continue
                casm=CAS_RE.search(line); cas=casm.group(0) if casm else ''
                # When two percentages appear, follow the old %Rel/%Abs convention.
                rel=abs_=None
                if len(pcts)>=2:
                    rel=float(pcts[-2].group(1)); abs_=float(pcts[-1].group(1)); pct=abs_
                else:
                    pct=float(pcts[-1].group(1))
                first_pct=pcts[0]
                name=line[:first_pct.start()].strip(' \t|-:')
                if casm: name=name.replace(cas,'').strip(' \t|-:')
                if name:
                    text_rows.append(GCMSRow(name=name,percent=pct,cas=cas,source=f'{path.name}:p{pageno}',
                                             percent_rel=rel,percent_abs=abs_))
        if text_rows:
            return text_rows
    except Exception:
        pass

    # Same Tesseract position-based approach as the user's old calculator, rendered via PyMuPDF.
    try:
        from perfume_studio.services.legacy_gcms_ocr import rows_from_scanned_pdf
        return _ocr_dicts_to_rows(rows_from_scanned_pdf(path))
    except Exception as e:
        raise ValueError(f'Could not read PDF as text/table and OCR fallback failed: {e}')


def import_file(path: str | Path):
    ext=Path(path).suffix.lower()
    if ext in {'.csv','.tsv','.txt'}: return import_csv(path)
    if ext in {'.xlsx','.xlsm'}: return import_xlsx(path)
    if ext=='.pdf': return import_pdf(path)
    if ext in {'.png','.jpg','.jpeg','.bmp','.tif','.tiff','.webp'}: return import_image(path)
    raise ValueError(f'Unsupported file type: {ext}')


def _cas_tokens(value: str):
    return {m.group(0) for m in CAS_RE.finditer(str(value or ''))}


def match_inventory(rows: Iterable[GCMSRow], materials, cutoff=0.68):
    mats=list(materials)
    by_cas={}
    for m in mats:
        for cas in _cas_tokens(m['cas']):
            by_cas.setdefault(cas,m)
    names={_norm(m['name']):m for m in mats}
    name_keys=list(names)
    out=[]
    for row in rows:
        row.matched_material_id=None; row.matched_material_name=''; row.score=0.0
        matched=None
        for cas in _cas_tokens(row.cas):
            if cas in by_cas:
                matched=by_cas[cas]; row.score=1.0; break
        if matched is None:
            key=_norm(row.name)
            if key in names:
                matched=names[key]; row.score=1.0
            else:
                matches=difflib.get_close_matches(key,name_keys,n=1,cutoff=cutoff)
                if matches:
                    mk=matches[0]; matched=names[mk]
                    row.score=difflib.SequenceMatcher(None,key,mk).ratio()
        if matched is not None:
            row.matched_material_id=matched['id']; row.matched_material_name=matched['name']
        out.append(row)
    return out
