from __future__ import annotations

"""OCR helpers ported/adapted from the user's PerfumeCalculator repository.

The original perfume_tool/ocr.py uses Tesseract word-position OCR and row grouping for
non-grid formula tables such as: Formula entry | Weight (g) | % Rel | % Abs.
This module keeps that approach but emits GCMS rows directly.
"""

from dataclasses import dataclass
from pathlib import Path
import os
import re
import shutil
import subprocess
import sys
from typing import Iterable

try:
    from PIL import Image, ImageGrab, ImageOps, ImageEnhance, ImageFilter
except Exception:
    Image = ImageGrab = ImageOps = ImageEnhance = ImageFilter = None

try:
    import pytesseract
except Exception:
    pytesseract = None

CAS_RE = re.compile(r'\b\d{2,7}-\d{2}-\d\b')
NUM_RE = re.compile(r'^\d+(?:[.,]\d+)?%?$')


@dataclass
class OCRWord:
    text: str
    left: int
    top: int
    width: int
    height: int
    conf: float

    @property
    def center_y(self) -> float:
        return self.top + self.height / 2


def _runtime_roots() -> list[Path]:
    roots: list[Path] = []
    if getattr(sys, 'frozen', False):
        roots.append(Path(sys.executable).resolve().parent)
        roots.append(Path(getattr(sys, '_MEIPASS', roots[0])))
    else:
        roots.append(Path(__file__).resolve().parents[2])
        roots.append(Path.cwd())
    out=[]
    for x in roots:
        if x not in out:
            out.append(x)
    return out


def configure_tesseract() -> bool:
    if pytesseract is None:
        return False
    # Portable/bundled locations first. Keep compatibility with the user's older calculator.
    for root in _runtime_roots():
        for folder in ('tesseract', 'Tesseract', 'Tesseract-OCR', 'tools/Tesseract-OCR'):
            exe = root / folder / 'tesseract.exe'
            tessdata = root / folder / 'tessdata'
            if exe.exists():
                pytesseract.pytesseract.tesseract_cmd = str(exe)
                if tessdata.is_dir():
                    os.environ['TESSDATA_PREFIX'] = str(tessdata)
                return True
    existing = shutil.which('tesseract')
    if existing:
        pytesseract.pytesseract.tesseract_cmd = existing
        td = Path(existing).parent / 'tessdata'
        if td.is_dir():
            os.environ['TESSDATA_PREFIX'] = str(td)
        return True
    candidates = [
        Path(r'C:\Program Files\Tesseract-OCR\tesseract.exe'),
        Path(r'C:\Program Files (x86)\Tesseract-OCR\tesseract.exe'),
    ]
    local = os.environ.get('LOCALAPPDATA')
    if local:
        candidates += [Path(local) / 'Programs' / 'Tesseract-OCR' / 'tesseract.exe',
                       Path(local) / 'Tesseract-OCR' / 'tesseract.exe']
    for exe in candidates:
        if exe.exists():
            pytesseract.pytesseract.tesseract_cmd = str(exe)
            td = exe.parent / 'tessdata'
            if td.is_dir():
                os.environ['TESSDATA_PREFIX'] = str(td)
            return True
    return False


def _try_install_tesseract_windows() -> bool:
    """Best-effort automatic installation; no manual web search is required.

    On Windows we use winget when available.  The call is silent and only runs when OCR is first
    requested and no local/system Tesseract can be found.
    """
    if os.name != 'nt':
        return False
    winget = shutil.which('winget')
    if not winget:
        return False
    flags = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
    # Winget currently exposes more than one Windows Tesseract package. Try the newer package
    # identifier first, then the long-standing UB Mannheim build as a fallback. Installer URLs can
    # occasionally break independently, so treating either package as sufficient is more robust.
    package_ids = ('tesseract-ocr.tesseract', 'UB-Mannheim.TesseractOCR')
    for package_id in package_ids:
        try:
            subprocess.run([
                winget, 'install', '--id', package_id, '--exact', '--silent',
                '--accept-package-agreements', '--accept-source-agreements',
            ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=300,
               creationflags=flags, check=False)
        except Exception:
            continue
        if configure_tesseract():
            return True
    return False

def ensure_tesseract_ready() -> None:
    if pytesseract is None or Image is None:
        raise RuntimeError('OCR dependencies are missing. Re-run run.bat so Pillow and pytesseract are installed.')
    if configure_tesseract():
        return
    if _try_install_tesseract_windows():
        return
    raise RuntimeError(
        'Tesseract OCR could not be started. Perfume Studio tried portable/system locations and '
        'automatic winget installation. If winget is unavailable, place a Tesseract-OCR folder '
        'next to PerfumeStudio.exe (or next to app.py when running from source).'
    )

def clean_token(text: str) -> str:
    s = str(text or '').strip()
    for a,b in {'\r':'','|':'','—':'-','–':'-','“':'"','”':'"','‘':"'",'’':"'",'\u00a0':' ','％':'%'}.items():
        s=s.replace(a,b)
    return s.strip()


def clean_ingredient(text: str) -> str:
    s=clean_token(text).strip(' -–—,;:|')
    s=re.sub(r'([a-z])([A-Z])', r'\1 \2', s)
    compact=s.replace(' ','')
    fixes={
        'ISOESuper':'ISO E Super','IsoESuper':'Iso E Super','AmbroxSuper':'Ambrox Super',
        'EthylLinalol':'Ethyl linalol','EthylLinalool':'Ethyl linalool',
        'EthyleneBrassylate':'Ethylene brassylate','Dihydromyrcenol':'Dihydromyrcenol',
        'VioletLeafAbsolute':'Violet Leaf Absolute','SechuanPepper':'Sechuan Pepper',
    }
    s=fixes.get(compact,s)
    return re.sub(r'\s+',' ',s).strip()


def preprocess_image(image):
    image=image.convert('RGB')
    w,h=image.size
    scale=3
    if w*scale>5500:
        scale=max(1,int(5500/max(w,1)))
    image=image.resize((w*scale,h*scale),Image.Resampling.LANCZOS)
    image=image.convert('L')
    image=ImageOps.autocontrast(image)
    image=image.filter(ImageFilter.MedianFilter(size=3))
    image=ImageEnhance.Contrast(image).enhance(2.2)
    image=ImageEnhance.Sharpness(image).enhance(1.8)
    image=image.point(lambda p:255 if p>185 else 0)
    return image


def get_words(image, psm: int = 6) -> list[OCRWord]:
    ensure_tesseract_ready()
    processed=preprocess_image(image)
    config=(f'--oem 3 --psm {psm} '
            '-c preserve_interword_spaces=1')
    data=pytesseract.image_to_data(processed,lang='eng',config=config,output_type=pytesseract.Output.DICT)
    out=[]
    for i,raw in enumerate(data.get('text',[])):
        text=clean_token(raw)
        if not text:
            continue
        try: conf=float(data['conf'][i])
        except Exception: conf=-1
        if conf < -1:
            continue
        out.append(OCRWord(text,int(data['left'][i]),int(data['top'][i]),int(data['width'][i]),int(data['height'][i]),conf))
    return out


def group_rows(words: Iterable[OCRWord]) -> list[list[OCRWord]]:
    words=sorted(list(words),key=lambda w:(w.center_y,w.left))
    if not words:
        return []
    heights=sorted(w.height for w in words if w.height>0)
    median_h=heights[len(heights)//2] if heights else 20
    threshold=max(10,median_h*0.75)
    rows=[]
    for word in words:
        placed=False
        for row in rows:
            y=sum(w.center_y for w in row)/len(row)
            if abs(word.center_y-y)<=threshold:
                row.append(word);placed=True;break
        if not placed:
            rows.append([word])
    for row in rows: row.sort(key=lambda w:w.left)
    rows.sort(key=lambda r:sum(w.center_y for w in r)/len(r))
    return rows


def _number(text: str):
    s=clean_token(text).replace(',','.').replace('%','')
    if not re.fullmatch(r'\d+(?:\.\d+)?',s):
        return None
    try:return float(s)
    except Exception:return None


def parse_word_row(row: list[OCRWord]) -> dict | None:
    """Interpret material rows using right-to-left numeric columns.

    Supports both the old calculator shape (Material | Weight | %Rel | %Abs) and simple
    two-column recipe screenshots (Material | Grams/Parts).
    """
    if not row:
        return None
    words=sorted(row,key=lambda w:w.left)
    texts=[w.text for w in words]
    joined=' '.join(texts)
    low=re.sub(r'[^a-z0-9% ]+',' ',joined.lower())
    if any(x in low for x in ('formula entry','ingredient percentage','material weight','% rel','% abs','percentage amount')):
        return None
    if re.match(r'^\s*total\b', joined, re.I):
        return None

    numeric=[]; nonnum=[]; cas=''
    for w in words:
        cm=CAS_RE.fullmatch(clean_token(w.text))
        if cm:
            cas=cm.group(0); continue
        val=_number(w.text)
        if val is not None:numeric.append((w.left,val,w.text))
        else:nonnum.append(w)
    if not numeric:return None
    numeric.sort(key=lambda x:x[0]); vals=[x[1] for x in numeric]
    weight=pct_rel=pct_abs=None; predilution_pct=None
    if len(vals)>=3:
        weight=vals[-3]; pct_rel=vals[-2] if 0<=vals[-2]<=100 else None; pct_abs=vals[-1] if 0<=vals[-1]<=100 else None
    elif len(vals)==2:
        first_text=numeric[-2][2]; second_text=numeric[-1][2]
        # Formula screenshots commonly encode stock dilution inside the material label, e.g.
        # `Damascone Alpha 10% | 5`. Preserve that as predilution rather than treating 10/5 as
        # GCMS %Rel/%Abs.
        if '%' in first_text and '%' not in second_text:
            predilution_pct=vals[-2] if 0<vals[-2]<=100 else None
            weight=vals[-1]
        elif '%' in first_text and '%' in second_text:
            predilution_pct=None; pct_rel,pct_abs=vals[-2],vals[-1]
        elif 0<=vals[-2]<=100 and 0<=vals[-1]<=100:
            predilution_pct=None; pct_rel,pct_abs=vals[-2],vals[-1]
        elif 0<=vals[-1]<=100:
            predilution_pct=None; weight,pct_abs=vals[-2],vals[-1]
        else:
            predilution_pct=None; weight=vals[-1]
    else:
        weight=vals[-1]
        if '%' in numeric[-1][2] and 0<=vals[-1]<=100:
            pct_abs=vals[-1]; weight=None

    first_num_x=numeric[0][0]
    name_words=[w.text for w in nonnum if w.left < first_num_x] or [w.text for w in nonnum]
    name=clean_ingredient(' '.join(name_words))
    if not re.search(r'[A-Za-z]',name) or len(name)<=1:return None
    if name.lower() in {'total','formula','ingredient','material','compound','grams','gram','parts'}:return None
    if weight is None and pct_abs is None and pct_rel is None:return None
    return {'name':name,'cas':cas,'weight':weight,'predilution_pct':predilution_pct,'percent_rel':pct_rel,'percent_abs':pct_abs}

def rows_from_image(image, source: str = 'OCR') -> list[dict]:
    words=get_words(image,psm=6)
    parsed=[]
    for row in group_rows(words):
        x=parse_word_row(row)
        if x:
            x['source']=source
            parsed.append(x)
    if len(parsed)<2:
        # A different layout sometimes works better with sparse text mode.
        words=get_words(image,psm=11)
        parsed=[]
        for row in group_rows(words):
            x=parse_word_row(row)
            if x:
                x['source']=source
                parsed.append(x)
    return parsed


def rows_from_image_path(path: str | Path) -> list[dict]:
    ensure_tesseract_ready()
    p=Path(path)
    image=Image.open(p)
    return rows_from_image(image,p.name)


def rows_from_clipboard() -> list[dict]:
    ensure_tesseract_ready()
    image=ImageGrab.grabclipboard()
    if image is None or not hasattr(image,'convert'):
        raise ValueError('Clipboard does not contain an image.')
    return rows_from_image(image,'Clipboard image')


def rows_from_scanned_pdf(path: str | Path, max_pages: int = 30) -> list[dict]:
    ensure_tesseract_ready()
    try:
        import fitz  # PyMuPDF
    except Exception as e:
        raise RuntimeError('PyMuPDF is required for scanned-PDF OCR. Re-run run.bat.') from e
    p=Path(path)
    doc=fitz.open(str(p))
    out=[]
    try:
        for i,page in enumerate(doc):
            if i>=max_pages: break
            pix=page.get_pixmap(matrix=fitz.Matrix(2.0,2.0),alpha=False)
            image=Image.frombytes('RGB',[pix.width,pix.height],pix.samples)
            out.extend(rows_from_image(image,f'{p.name}:p{i+1} OCR'))
    finally:
        doc.close()
    return out
