from __future__ import annotations

"""Unified formula import/export with one invariant: formula items are parts-per-thousand.

All imported compositions are converted to ACTIVE material and normalized so the stored sum is exactly 1000.
Material concentration/predilution remains an Inventory property used only for weighing, so changing
Inventory concentration later changes required stock weight without changing formula composition.
"""

from dataclasses import dataclass
from pathlib import Path
import csv
import re
import xml.etree.ElementTree as ET
from datetime import datetime

from perfume_studio.services.inventory_enrichment import parse_inventory_line, normalize_material_name
from perfume_studio.services.transparency_lookup import resolve_local_identity
from perfume_studio.services.legacy_formula_xml import read_formulas_from_xml
from perfume_studio.services.gcms_importer import import_file as import_analysis_file

NUM_RE = re.compile(r'(?<!\S)(\d+(?:[.,]\d+)?)\s*$')
TITLE_RE = re.compile(r'^[A-Za-z]{2,}(?:\s+|[-_])?\d{3,}$')
TOTAL_RE = re.compile(r'^\s*total\b', re.I)


@dataclass
class ImportedFormulaRow:
    name: str
    raw_value: float
    predilution_pct: float = 100.0
    solvent: str = ''
    cas: str = ''
    source_is_active: bool = False


@dataclass
class ImportedFormula:
    name: str
    rows: list[ImportedFormulaRow]
    source: str = ''
    unit: str = 'parts'
    batch_g: float = 100.0
    fragrance_load_pct: float = 20.0
    notes: str = ''


def normalize_values_to_1000(values: list[float]) -> list[float]:
    nums = [max(0.0, float(v or 0)) for v in values]
    total = sum(nums)
    if total <= 0:
        return [0.0 for _ in nums]
    scaled = [v * 1000.0 / total for v in nums]
    # Floating arithmetic can make the sum 999.99999999. Put the residue on the largest row.
    residue = 1000.0 - sum(scaled)
    if scaled:
        idx = max(range(len(scaled)), key=lambda i: scaled[i])
        scaled[idx] += residue
    return scaled


def normalized_formula_lines(lines):
    """Return (line, normalized_parts) pairs for any objects with .parts."""
    vals = normalize_values_to_1000([float(x.parts or 0) for x in lines])
    return list(zip(lines, vals))


def _clean_material_text(name: str) -> str:
    s = (name or '').strip()
    s = s.replace('™','').replace('®','').replace('©','')
    s = re.sub(r'\s+', ' ', s)
    return s.strip(' \t|-:')


def _extract_formula_name(text: str, fallback: str) -> str:
    lines=[re.sub(r'\s+',' ',x).strip() for x in (text or '').splitlines() if x.strip()]
    for line in reversed(lines):
        if TITLE_RE.fullmatch(line) and not re.search(r'copyright|total',line,re.I):
            return line
    for line in lines[:12]:
        if TITLE_RE.fullmatch(line):
            return line
    return fallback


def _parse_text_weight_formula(text: str, source: str, fallback_name: str) -> ImportedFormula | None:
    """Parse ordinary two-column recipe PDFs/text, e.g. `Hedione 200` under a Grams header.

    This deliberately requires multiple consecutive material+number rows and ignores prose/footer
    lines.  A trailing percentage in the material name (e.g. Damascone Alpha 10%) is interpreted as
    Inventory predilution, while the final number is the formula amount.
    """
    rows=[]
    started=False
    for raw in (text or '').splitlines():
        line=re.sub(r'\s+',' ',raw).strip()
        if not line:
            continue
        if TOTAL_RE.match(line):
            if rows:
                break
            continue
        if re.fullmatch(r'grams?|parts?|weight(?:\s*\(g\))?', line, re.I):
            started=True
            continue
        # Supplier formula titles often look like `ANI 898307`: syntactically they resemble a
        # material followed by a large amount. Detect the whole title before splitting its number.
        if TITLE_RE.fullmatch(line):
            continue
        m=re.search(r'(?<!\S)(\d+(?:[.,]\d+)?)\s*%?\s*$', line)
        if not m:
            # Once a table has started, prose after several rows usually means table end/footer.
            continue
        value_text=m.group(1)
        try:value=float(value_text.replace(',','.'))
        except Exception:continue
        name=_clean_material_text(line[:m.start()].strip())
        if not name or len(name)<2 or not re.search(r'[A-Za-z]',name):
            continue
        low=name.lower()
        if low.startswith(('©','copyright','nz','page ')) or 'liability' in low or 'formula is provided' in low:
            continue
        if value <= 0:
            continue
        parsed=parse_inventory_line(name)
        cleaned=_clean_material_text(parsed.name)
        if not cleaned:
            continue
        rows.append(ImportedFormulaRow(cleaned,value,parsed.predilution_pct,parsed.solvent,parsed.cas))
        started=True
    # Avoid treating arbitrary prose with one terminal number as a formula.
    if len(rows)<3:
        return None
    # Remove obvious accidental title/footer rows by keeping the main table block. Since formulas
    # normally have a strongly varying amount distribution, TITLE-like material rows are excluded.
    rows=[r for r in rows if not TITLE_RE.fullmatch(r.name)]
    if len(rows)<3:
        return None
    return ImportedFormula(_extract_formula_name(text,fallback_name),rows,source=source,unit='weight')


def import_pdf_formula(path: str | Path) -> list[ImportedFormula]:
    path=Path(path)
    texts=[]
    try:
        from pypdf import PdfReader
        reader=PdfReader(path)
        for i,page in enumerate(reader.pages,1):
            txt=page.extract_text() or ''
            if txt.strip():texts.append((i,txt))
    except Exception:
        texts=[]
    out=[]
    for pageno,txt in texts:
        f=_parse_text_weight_formula(txt,f'{path.name}:p{pageno}',path.stem)
        if f:out.append(f)
    if out:
        # One-page supplier formulas are common. Multi-page docs are kept as one formula when the
        # inferred title is the same; otherwise they remain separate formulas.
        if len(out)>1 and len({normalize_material_name(x.name) for x in out})==1:
            rows=[]
            for f in out:rows.extend(f.rows)
            return [ImportedFormula(out[0].name,rows,source=path.name,unit='weight')]
        return out

    # Table/percentage/text extraction from the old GCMS pipeline, then OCR fallback if needed.
    analysis=import_analysis_file(path)
    if not analysis:
        return []
    rows=[]
    for row in analysis:
        parsed=parse_inventory_line(row.name)
        pred=row.predilution_pct if getattr(row,'predilution_pct',None) else parsed.predilution_pct
        if TITLE_RE.fullmatch(f'{parsed.name} {int(row.percent) if float(row.percent).is_integer() else row.percent}'):
            continue
        rows.append(ImportedFormulaRow(parsed.name,row.percent,pred,parsed.solvent,row.cas or parsed.cas))
    return [ImportedFormula(path.stem,rows,source=path.name,unit='analysis')]


def import_image_formula(path: str | Path) -> list[ImportedFormula]:
    analysis=import_analysis_file(path)
    rows=[]
    for row in analysis:
        parsed=parse_inventory_line(row.name)
        pred=row.predilution_pct if getattr(row,'predilution_pct',None) else parsed.predilution_pct
        if TITLE_RE.fullmatch(f'{parsed.name} {int(row.percent) if float(row.percent).is_integer() else row.percent}'):
            continue
        rows.append(ImportedFormulaRow(parsed.name,row.percent,pred,parsed.solvent,row.cas or parsed.cas))
    return [ImportedFormula(Path(path).stem,rows,source=Path(path).name,unit='analysis')] if rows else []


def import_tabular_formula(path: str | Path) -> list[ImportedFormula]:
    # First use the existing GCMS/import table logic (including %Rel/%Abs handling).
    analysis=import_analysis_file(path)
    rows=[]
    for row in analysis:
        parsed=parse_inventory_line(row.name)
        pred=row.predilution_pct if getattr(row,'predilution_pct',None) else parsed.predilution_pct
        if TITLE_RE.fullmatch(f'{parsed.name} {int(row.percent) if float(row.percent).is_integer() else row.percent}'):
            continue
        rows.append(ImportedFormulaRow(parsed.name,row.percent,pred,parsed.solvent,row.cas or parsed.cas))
    if rows:
        return [ImportedFormula(Path(path).stem,rows,source=Path(path).name,unit='analysis')]

    # Generic two-column CSV/TSV without percentage headers: name + amount/grams/parts.
    p=Path(path)
    if p.suffix.lower() in {'.csv','.tsv','.txt'}:
        text=p.read_text(encoding='utf-8-sig',errors='replace')
        try:dialect=csv.Sniffer().sniff(text[:4096],delimiters=',\t;|')
        except Exception:dialect=csv.excel_tab if '\t' in text[:4096] else csv.excel
        matrix=list(csv.reader(text.splitlines(),dialect))
        generic=[]
        for row in matrix:
            if len(row)<2:continue
            val=None;idx=None
            for i in range(len(row)-1,0,-1):
                try:
                    val=float(str(row[i]).replace('%','').replace(',','.').strip())
                    idx=i;break
                except Exception:pass
            if val is None or val<=0:continue
            name=_clean_material_text(str(row[0]))
            if not name or normalize_material_name(name) in {'name','material','ingredient','formula entry','total'}:continue
            parsed=parse_inventory_line(name)
            generic.append(ImportedFormulaRow(parsed.name,val,parsed.predilution_pct,parsed.solvent,parsed.cas))
        if generic:return [ImportedFormula(p.stem,generic,source=p.name,unit='amount')]
    return []


def import_xml_formula(path: str | Path) -> list[ImportedFormula]:
    """Import legacy/new XML and preserve active-composition semantics.

    Both new Perfume Studio XML and the legacy Author50CO calculator store ``row.part`` as pure-material-equivalent (ACTIVE) parts. Legacy ``manual_dilution`` only tells the calculator which stock concentration to weigh; it must not be multiplied into ``part`` again.
    """
    path = Path(path)
    root = ET.parse(path).getroot()
    nodes = [root] if root.tag == 'formula' else list(root.findall('formula')) if root.tag == 'formulas' else []
    if not nodes:
        raise ValueError('Invalid formula XML file. Expected <formula> or <formulas>.')
    out=[]
    for elem in nodes:
        modern_active = (elem.get('parts_format') or '').strip().lower() in {'per_thousand','parts_per_thousand','active_per_thousand'}
        meta=elem.find('metadata');inputs=elem.find('inputs');rows_node=elem.find('rows')
        name=((meta.findtext('name') if meta is not None else '') or path.stem).strip()
        notes=((meta.findtext('description') if meta is not None else '') or '').strip()
        try: batch=float((inputs.findtext('target_weight') if inputs is not None else '') or 100)
        except Exception: batch=100.0
        try: load=float((inputs.findtext('desired_dilution') if inputs is not None else '') or 20)
        except Exception: load=20.0
        rows=[]
        if rows_node is not None:
            for node in rows_node.findall('row'):
                material=(node.findtext('material') or '').strip()
                if not material: continue
                try: part=float((node.findtext('part') or '0').replace(',','.'))
                except Exception: part=0.0
                if part <= 0: continue
                raw_dil=(node.findtext('manual_dilution') or node.findtext('parsed_dilution') or '').strip()
                try:pct=float(raw_dil.replace('%','').replace(',','.')) if raw_dil else 100.0
                except Exception:pct=100.0
                if not (0 < pct <= 100): pct=100.0
                rows.append(ImportedFormulaRow(material,part,pct,'','',True))
        out.append(ImportedFormula(name,rows,source=path.name,unit='active_parts',batch_g=batch,fragrance_load_pct=load,notes=notes))
    return out

def parse_formula_txt_document(text: str, fallback_name: str = 'Imported Formula') -> ImportedFormula | None:
    """Parse the human-readable Perfume Studio TXT format.

    Format:
        title - Formula Name
        180 - Hedione
        170 - Hedione HC

        note - free text

    Numeric values are ACTIVE parts and are normalized to 1000 on storage.
    """
    title=(fallback_name or 'Imported Formula').strip() or 'Imported Formula'
    note=''
    rows=[]
    saw_explicit=False
    for raw in (text or '').splitlines():
        line=raw.strip()
        if not line:
            continue
        m=re.match(r'^title\s*-\s*(.*)$',line,re.I)
        if m:
            title=(m.group(1).strip() or title);saw_explicit=True;continue
        m=re.match(r'^note\s*-\s*(.*)$',line,re.I)
        if m:
            note=m.group(1).strip();saw_explicit=True;continue
        m=re.match(r'^([+-]?\d+(?:[.,]\d+)?)\s*-\s*(.+?)\s*$',line)
        if m:
            try:value=float(m.group(1).replace(',','.'))
            except Exception:continue
            material=_clean_material_text(m.group(2))
            if value>0 and material:
                parsed=parse_inventory_line(material)
                cleaned=_clean_material_text(parsed.name)
                if cleaned:
                    rows.append(ImportedFormulaRow(cleaned,value,parsed.predilution_pct,parsed.solvent,parsed.cas,True))
                    saw_explicit=True
    if not rows or not saw_explicit:
        return None
    return ImportedFormula(title,rows,source='txt',unit='active_parts',notes=note)


def import_formula_file(path: str | Path) -> list[ImportedFormula]:
    p=Path(path);ext=p.suffix.lower()
    if ext=='.xml':return import_xml_formula(p)
    if ext=='.pdf':return import_pdf_formula(p)
    if ext in {'.png','.jpg','.jpeg','.bmp','.tif','.tiff','.webp'}:return import_image_formula(p)
    if ext=='.txt':
        text=p.read_text(encoding='utf-8-sig',errors='replace')
        direct=parse_formula_txt_document(text,p.stem)
        if direct:return [direct]
        return import_tabular_formula(p)
    if ext in {'.csv','.tsv','.xlsx','.xlsm'}:return import_tabular_formula(p)
    raise ValueError(f'Unsupported formula file type: {ext}')



def _material_predilution_options(material) -> list[float]:
    raw=''
    try:raw=material['predilutions'] or ''
    except Exception:pass
    if not str(raw).strip():
        try:raw=str(float(material['concentration_pct'] or 100))
        except Exception:raw='100'
    out=[]
    for token in re.split(r'[,;/]+',str(raw)):
        token=token.strip().replace('%','')
        if not token:continue
        try:v=float(token)
        except Exception:continue
        if 0<v<=100 and not any(abs(v-x)<1e-9 for x in out):out.append(v)
    return out or [100.0]

def _selected_predilution(material, preferred: float | None) -> float:
    opts=_material_predilution_options(material)
    try:p=float(preferred)
    except Exception:p=opts[0]
    exact=next((x for x in opts if abs(x-p)<0.001),None)
    if exact is not None:return exact
    return min(opts,key=lambda x:abs(x-p))

def _find_material(db,name: str,pct: float,cas: str=''):
    """Resolve an imported identity to an existing Inventory stock row without creating one."""
    norm=normalize_material_name(name)
    identity=resolve_local_identity(db,name)
    lookup_cas=(cas or (identity['cas'] if identity else '') or '').strip()
    candidates=[]
    for m in db.list_materials():
        same_name=normalize_material_name(m['name'])==norm
        material_cas=str(m['cas'] or '')
        same_cas=bool(lookup_cas and lookup_cas in material_cas)
        if same_name or same_cas:candidates.append(m)
    if not candidates:return None
    for m in candidates:
        if any(abs(x-pct)<0.001 for x in _material_predilution_options(m)):return m
    if len(candidates)==1:return candidates[0]
    def key(m):
        opts=_material_predilution_options(m);closest=min(abs(x-pct) for x in opts);mx=max(opts)
        return (closest, 0 if any(x>=99.999 for x in opts) else 1, -mx)
    return sorted(candidates,key=key)[0]

def _create_material(db,row: ImportedFormulaRow) -> int:
    identity=resolve_local_identity(db,row.name)
    cas=row.cas or (identity['cas'] if identity else '')
    mtype='dilution' if row.predilution_pct<99.999 else 'raw'
    return db.execute('''INSERT INTO materials(name,cas,material_type,concentration_pct,solvent,unit_cost_per_g,currency,stock_g)
                         VALUES(?,?,?,?,?,?,?,?)''',
                      (row.name,cas,mtype,row.predilution_pct,row.solvent,0.0,'USD',0.0))


def _unique_name(db,preferred: str) -> str:
    base=(preferred or 'Imported Formula').strip() or 'Imported Formula'
    used={r['name'] for r in db.query('SELECT name FROM formulas')}
    if base not in used:return base
    i=2
    while f'{base} ({i})' in used:i+=1
    return f'{base} ({i})'


def parse_pasted_formula_text(text: str, name: str = 'Pasted Formula') -> ImportedFormula:
    """Parse pasted formula text in either ``parts - material`` or ``material parts`` form."""
    direct=parse_formula_txt_document(text,name)
    if direct:
        # Paste-dialog name wins unless the pasted text explicitly provided a title.
        if not re.search(r'^\s*title\s*-',text or '',re.I|re.M):
            direct.name=(name or 'Pasted Formula').strip() or 'Pasted Formula'
        return direct
    rows=[];note=''
    for raw in (text or '').splitlines():
        line=re.sub(r'\s+',' ',raw.replace('\t',' ')).strip()
        if not line or TOTAL_RE.match(line):
            continue
        nm=re.match(r'^note\s*-\s*(.*)$',line,re.I)
        if nm:
            note=nm.group(1).strip();continue
        if re.fullmatch(r'(material|ingredient|formula entry)(?:\s+parts?|\s+weight)?',line,re.I):
            continue
        m=NUM_RE.search(line)
        if not m:
            continue
        try:value=float(m.group(1).replace(',','.'))
        except Exception:continue
        if value<=0:continue
        material=_clean_material_text(line[:m.start()].strip())
        if not material:continue
        parsed=parse_inventory_line(material);cleaned=_clean_material_text(parsed.name)
        if cleaned:rows.append(ImportedFormulaRow(cleaned,value,parsed.predilution_pct,parsed.solvent,parsed.cas,True))
    if not rows:raise ValueError('No formula rows detected. Paste “180 - Hedione” or one material and one numeric parts value per line.')
    return ImportedFormula((name or 'Pasted Formula').strip() or 'Pasted Formula',rows,source='clipboard',unit='active_parts',notes=note)


def store_imported_formulas(db, formulas: list[ImportedFormula], create_missing_materials: bool=False,
                            overwrite_existing: bool=True) -> dict:
    """Store imported formulas using name-based overwrite semantics.

    Formula identity stays independent from Inventory. When ``overwrite_existing`` is true, an
    imported formula with an existing title reuses that formula id and replaces its recipe rows.
    UI callers make the requested TXT backup before invoking this function.
    """
    created=[];unmatched=[];overwritten=[]
    for formula in formulas:
        valid=[r for r in formula.rows if r.name and float(r.raw_value or 0)>0]
        if not valid:continue
        active_values=[float(r.raw_value) if r.source_is_active else float(r.raw_value)*max(0.0,min(100.0,float(r.predilution_pct or 100)))/100.0 for r in valid]
        normalized=normalize_values_to_1000(active_values)
        preferred=(formula.name or 'Imported Formula').strip() or 'Imported Formula'
        existing=db.query('SELECT id FROM formulas WHERE name=?',(preferred,)) if overwrite_existing else []
        if existing:
            fid=int(existing[0]['id']);name=preferred;overwritten.append(name)
            db.execute('UPDATE formulas SET batch_g=?,fragrance_load_pct=?,ifra_category=?,notes=? WHERE id=?',
                       (formula.batch_g if formula.batch_g>0 else 100.0,
                        formula.fragrance_load_pct if 0<formula.fragrance_load_pct<=100 else 20.0,'4',formula.notes or '',fid))
            db.execute('DELETE FROM formula_items WHERE formula_id=?',(fid,))
        else:
            name=_unique_name(db,preferred) if not overwrite_existing else preferred
            fid=db.execute('INSERT INTO formulas(name,batch_g,fragrance_load_pct,ifra_category,notes) VALUES(?,?,?,?,?)',
                           (name,formula.batch_g if formula.batch_g>0 else 100.0,
                            formula.fragrance_load_pct if 0<formula.fragrance_load_pct<=100 else 20.0,'4',formula.notes or ''))
        created.append(fid);pending=[]
        for order,(row,parts) in enumerate(zip(valid,normalized)):
            m=_find_material(db,row.name,row.predilution_pct,row.cas);mid=m['id'] if m else None
            if mid is None:unmatched.append(row.name)
            selected_name=m['name'] if m else row.name
            selected_pred=_selected_predilution(m,row.predilution_pct) if m else None
            pending.append((fid,mid,selected_name,row.name,0,selected_pred,parts,order))
        if pending:db.executemany('INSERT INTO formula_items(formula_id,material_id,material_name,original_material_name,disabled,selected_predilution_pct,parts,sort_order) VALUES(?,?,?,?,?,?,?,?)',pending)
    return {'formulas':len(created),'formula_ids':created,'created_materials':0,'unmatched':unmatched,'overwritten':overwritten}


def import_formula_to_db(db,path: str|Path,create_missing_materials: bool=False) -> dict:
    return store_imported_formulas(db,import_formula_file(path),create_missing_materials=False,overwrite_existing=True)


def export_formula_xml(db, formula_id: int, path: str|Path) -> str:
    frows=db.query('SELECT * FROM formulas WHERE id=?',(formula_id,))
    if not frows:raise ValueError('Formula not found.')
    f=frows[0]
    items=db.query("""SELECT fi.parts, fi.sort_order,
                             COALESCE(m.name,NULLIF(TRIM(fi.material_name),''),NULLIF(TRIM(fi.original_material_name),''),'') AS name
                      FROM formula_items fi LEFT JOIN materials m ON m.id=fi.material_id
                      WHERE fi.formula_id=? AND COALESCE(fi.disabled,0)=0
                      ORDER BY fi.sort_order,fi.id""",(formula_id,))
    values=normalize_values_to_1000([float(x['parts'] or 0) for x in items])
    # Serialize a normalized recipe, not Inventory stock concentrations. Round to stable decimal
    # parts and place any rounding residue on the final row so the exported displayed values total 1000.
    if values:
        values=[round(float(v),9) for v in values]
        values[-1]=round(values[-1] + (1000.0-sum(values)),9)
    root=ET.Element('formula');root.set('id',str(formula_id));root.set('parts_format','per_thousand');root.set('parts_semantics','active_material')
    meta=ET.SubElement(root,'metadata')
    ET.SubElement(meta,'name').text=f['name'];ET.SubElement(meta,'created_date').text=datetime.now().strftime('%Y-%m-%d')
    ET.SubElement(meta,'description').text=f['notes'] or '';ET.SubElement(meta,'saved_at').text=datetime.now().isoformat(timespec='seconds')
    inputs=ET.SubElement(root,'inputs')
    ET.SubElement(inputs,'target_weight').text=f'{float(f["batch_g"]):g}'
    ET.SubElement(inputs,'default_dilution').text='100'
    ET.SubElement(inputs,'desired_dilution').text=f'{float(f["fragrance_load_pct"]):g}'
    ET.SubElement(inputs,'maximum_dilution').text='100'
    ET.SubElement(root,'source_text').text=''
    rows_node=ET.SubElement(root,'rows')
    for item,parts in zip(items,values):
        node=ET.SubElement(rows_node,'row')
        ET.SubElement(node,'material').text=item['name']
        ET.SubElement(node,'part').text=f'{parts:.9f}'.rstrip('0').rstrip('.')
        ET.SubElement(node,'manual_dilution').text=''
        ET.SubElement(node,'part_adjusted_by_dilution').text='false'
        ET.SubElement(node,'parsed_part').text=f'{parts:.9f}'.rstrip('0').rstrip('.')
        ET.SubElement(node,'parsed_dilution').text=''
    try:ET.indent(root,space='  ')
    except Exception:pass
    ET.ElementTree(root).write(path,encoding='utf-8',xml_declaration=True)
    return str(path)


def normalize_all_formulas_to_1000(db) -> dict:
    """Idempotently normalize every existing formula to the storage invariant."""
    changed=0
    for f in db.query('SELECT id FROM formulas'):
        items=db.query('SELECT id,parts FROM formula_items WHERE formula_id=? ORDER BY sort_order,id',(f['id'],))
        vals=[float(x['parts'] or 0) for x in items]
        if not vals or sum(vals)<=0:continue
        normalized=normalize_values_to_1000(vals)
        if any(abs(a-b)>1e-8 for a,b in zip(vals,normalized)):
            with db.connect() as conn:
                conn.executemany('UPDATE formula_items SET parts=? WHERE id=?',[(v,item['id']) for item,v in zip(items,normalized)])
            changed+=1
    return {'normalized_formulas':changed}
