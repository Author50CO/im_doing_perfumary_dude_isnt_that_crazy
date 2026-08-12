from __future__ import annotations

from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP, getcontext
from pathlib import Path
import re

getcontext().prec = 40

_INVALID_FILENAME = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def safe_formula_filename(name: str) -> str:
    """Return a Windows-safe filename stem while keeping the title human-readable."""
    stem = _INVALID_FILENAME.sub('_', (name or 'Formula').strip()).rstrip(' .')
    return stem or 'Formula'


def formula_txt_path(data_dir: str | Path, title: str) -> Path:
    return Path(data_dir) / f'{safe_formula_filename(title)}.txt'


def _format_decimal(value: Decimal) -> str:
    if value == value.to_integral_value():
        return str(value.quantize(Decimal('1')))
    text = format(value.normalize(), 'f')
    return text.rstrip('0').rstrip('.') if '.' in text else text


def normalize_decimal_parts(values, places: int = 9) -> list[Decimal]:
    """Normalize positive values to exactly 1000 using stable decimal output.

    The final serialized numbers sum to Decimal('1000') exactly. A rounding residue is assigned
    to the largest row so a TXT round-trip never drifts away from parts-per-thousand.
    """
    nums = [max(Decimal('0'), Decimal(str(v or 0))) for v in values]
    total = sum(nums, Decimal('0'))
    if total <= 0:
        return [Decimal('0') for _ in nums]
    quantum = Decimal(1).scaleb(-places)
    scaled = [(v * Decimal('1000') / total).quantize(quantum, rounding=ROUND_HALF_UP) for v in nums]
    if scaled:
        residue = Decimal('1000') - sum(scaled, Decimal('0'))
        idx = max(range(len(scaled)), key=lambda i: nums[i])
        scaled[idx] += residue
    return scaled


def formula_text_from_db(db, formula_id: int) -> str:
    frows = db.query('SELECT * FROM formulas WHERE id=?', (formula_id,))
    if not frows:
        raise ValueError('Formula not found.')
    f = frows[0]
    items = db.query(
        """SELECT fi.parts, fi.sort_order,
                  COALESCE(m.name,NULLIF(TRIM(fi.material_name),''),NULLIF(TRIM(fi.original_material_name),''),'') AS name
           FROM formula_items fi LEFT JOIN materials m ON m.id=fi.material_id
           WHERE fi.formula_id=? AND COALESCE(fi.disabled,0)=0 AND fi.parts>0
           ORDER BY fi.sort_order,fi.id""",
        (formula_id,),
    )
    parts = normalize_decimal_parts([x['parts'] for x in items])
    lines = [f"title - {f['name']}"]
    for item, value in zip(items, parts):
        name = str(item['name'] or '').strip()
        if name:
            lines.append(f'{_format_decimal(value)} - {name}')
    note = re.sub(r'[\r\n]+', ' | ', str(f['notes'] or '').strip())
    lines.extend(['', f'note - {note}'])
    return '\n'.join(lines) + '\n'


def write_formula_txt(db, formula_id: int, data_dir: str | Path) -> Path:
    f = db.query('SELECT name FROM formulas WHERE id=?', (formula_id,))
    if not f:
        raise ValueError('Formula not found.')
    path = formula_txt_path(data_dir, f[0]['name'])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(formula_text_from_db(db, formula_id), encoding='utf-8')
    return path


def sync_all_formula_txt(db, data_dir: str | Path) -> list[Path]:
    out = []
    for row in db.query('SELECT id FROM formulas ORDER BY id'):
        out.append(write_formula_txt(db, int(row['id']), data_dir))
    return out


def backup_formula_txt(db, formula_id: int, data_dir: str | Path, now: datetime | None = None) -> Path:
    """Snapshot the pre-overwrite recipe to user_data/formula_backup.

    Requested filename shape: ``title-2026-aug-12 backup.txt``. If the same title is overwritten
    more than once on the same date, a numeric suffix is added rather than destroying a backup.
    """
    f = db.query('SELECT name FROM formulas WHERE id=?', (formula_id,))
    if not f:
        raise ValueError('Formula not found.')
    title = str(f[0]['name'])
    backup_dir = Path(data_dir) / 'formula_backup'
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = (now or datetime.now()).strftime('%Y-%b-%d').lower()
    stem = f'{safe_formula_filename(title)}-{stamp} backup'
    path = backup_dir / f'{stem}.txt'
    n = 2
    while path.exists():
        path = backup_dir / f'{stem} {n}.txt'
        n += 1
    path.write_text(formula_text_from_db(db, formula_id), encoding='utf-8')
    return path


def remove_formula_txt(data_dir: str | Path, title: str) -> None:
    path = formula_txt_path(data_dir, title)
    try:
        path.unlink()
    except FileNotFoundError:
        pass
