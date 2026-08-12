from __future__ import annotations
from pathlib import Path
import webbrowser
import re
import logging

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QKeySequence, QColor, QBrush
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QTabWidget, QTableWidget, QTableWidgetItem,
    QPushButton, QLabel, QLineEdit, QComboBox, QDoubleSpinBox, QMessageBox, QFileDialog,
    QDialog, QFormLayout, QTextEdit, QDialogButtonBox, QHeaderView, QAbstractItemView,
    QSpinBox, QGroupBox, QSplitter, QApplication, QMenu, QColorDialog, QStyledItemDelegate
)

from perfume_studio.services.formula_engine import FormulaLine, calculate_manufacturing_plan
from perfume_studio.services.ifra_importer import import_ifra_overview, import_ncs_annex
from perfume_studio.services.ifra_engine import check_formula
from perfume_studio.services.inventory_enrichment import (
    parse_inventory_line, build_ifra_name_index, enrich_name_from_ifra, normalize_material_name,
    material_name_candidates
)
from perfume_studio.services.transparency_lookup import (
    save_material_alias, lookup_saved_alias, resolve_local_identity
)
from perfume_studio.services.bundled_reference import ensure_bundled_ifra51, resource_data_dir
from perfume_studio.services.transparency_lookup import unresolved_identity_reason
from perfume_studio.services.resolution_candidates import (
    get_resolution_candidates, high_confidence_local_candidate, search_resolution_candidates_by_cas
)
from perfume_studio.services.formula_io import import_formula_file, normalize_values_to_1000, parse_pasted_formula_text, store_imported_formulas
from perfume_studio.services.formula_text import (write_formula_txt, sync_all_formula_txt, backup_formula_txt, remove_formula_txt, formula_text_from_db)
from perfume_studio.services.material_reference import ifra_details_for_cas, fetch_pubchem_by_cas, fetch_comptox_density_by_cas, save_reference_snapshot


def fnum(v, digits=4):
    try:
        return f'{float(v):.{digits}f}'
    except Exception:
        return ''


def material_label(material):
    name = material['name'] or ''
    try:
        pct = float(material['concentration_pct'] or 100)
    except Exception:
        pct = 100
    if abs(pct - 100.0) > 1e-9:
        return f'{name} [{pct:g}%]'
    return name


class MaterialDialog(QDialog):
    def __init__(self, db, material=None, parent=None):
        super().__init__(parent)
        self.db=db; self.material=material
        self.setWindowTitle('Material')
        form=QFormLayout(self)
        self.name=QLineEdit(); self.cas=QLineEdit(); self.type=QComboBox(); self.type.addItems(['raw','natural','dilution','base'])
        self.concentration=QDoubleSpinBox(); self.concentration.setRange(0,100); self.concentration.setDecimals(4); self.concentration.setValue(100)
        self.parent_combo=QComboBox(); self.parent_combo.addItem('(none)', None)
        for m in db.list_materials(): self.parent_combo.addItem(material_label(m), m['id'])
        self.solvent=QLineEdit(); self.density=QDoubleSpinBox(); self.density.setRange(0,10); self.density.setDecimals(5); self.density.setSpecialValueText('')
        self.supplier=QLineEdit(); self.cost=QDoubleSpinBox(); self.cost.setRange(0,1e9); self.cost.setDecimals(6)
        self.stock=QDoubleSpinBox(); self.stock.setRange(-1e9,1e9); self.stock.setDecimals(4)
        self.notes=QTextEdit(); self.notes.setMaximumHeight(80)
        for label,w in [('Name',self.name),('CAS',self.cas),('Type',self.type),('Concentration %',self.concentration),('Parent material',self.parent_combo),('Solvent',self.solvent),('Density g/mL',self.density),('Supplier',self.supplier),('Cost / g',self.cost),('Stock g',self.stock),('Notes',self.notes)]: form.addRow(label,w)
        buttons=QDialogButtonBox(QDialogButtonBox.Save|QDialogButtonBox.Cancel); buttons.accepted.connect(self.accept); buttons.rejected.connect(self.reject); form.addRow(buttons)
        if material:
            self.name.setText(material['name']); self.cas.setText(material['cas'] or ''); self.type.setCurrentText(material['material_type'])
            self.concentration.setValue(float(material['concentration_pct'] or 100));
            idx=self.parent_combo.findData(material['parent_material_id']); self.parent_combo.setCurrentIndex(max(0,idx))
            self.solvent.setText(material['solvent'] or ''); self.density.setValue(float(material['density'] or 0)); self.supplier.setText(material['supplier'] or '')
            self.cost.setValue(float(material['unit_cost_per_g'] or 0)); self.stock.setValue(float(material['stock_g'] or 0)); self.notes.setPlainText(material['notes'] or '')

    def values(self):
        # Legacy DB columns currency/location are kept only for backwards compatibility.
        # User-facing prices are unitless and are assumed to use one consistent currency.
        return (self.name.text().strip(), self.cas.text().strip(), self.type.currentText(), self.concentration.value(), self.parent_combo.currentData(), self.solvent.text().strip(), self.density.value() or None, self.supplier.text().strip(), self.cost.value(), '', self.stock.value(), '', self.notes.toPlainText().strip())


class NoteGroupDialog(QDialog):
    """Create a reusable scent group (name + color only)."""
    def __init__(self, parent=None, row=None):
        super().__init__(parent);self.setWindowTitle('Scent Group');self._color=(row['color_hex'] if row else '#7fbf7f') or '#7fbf7f'
        form=QFormLayout(self)
        self.name=QLineEdit((row['name'] if row else '') or '')
        self.color_button=QPushButton();self.color_button.clicked.connect(self.pick_color);self._update_color_button()
        form.addRow('Group name',self.name);form.addRow('Color',self.color_button)
        buttons=QDialogButtonBox(QDialogButtonBox.Save|QDialogButtonBox.Cancel);buttons.accepted.connect(self._validate_accept);buttons.rejected.connect(self.reject);form.addRow(buttons)
    def _update_color_button(self):
        self.color_button.setText(self._color.upper());self.color_button.setStyleSheet(f'background:{self._color};')
    def pick_color(self):
        c=QColorDialog.getColor(QColor(self._color),self,'Choose scent group color')
        if c.isValid():self._color=c.name();self._update_color_button()
    def _validate_accept(self):
        if not self.name.text().strip():QMessageBox.warning(self,'Scent Group','Group name is required.');return
        self.accept()
    def values(self):return self.name.text().strip(),self._color


class MaterialDetailsDialog(QDialog):
    """Editable material card with IFRA, PubChem/CompTox and reusable note groups."""
    def __init__(self, db, material, parent=None):
        super().__init__(parent);self.db=db;self.material=material;self.material_id=int(material['id']);self._pubchem=None;self._comptox=None;self._ifra=None
        self.setWindowTitle(f"Material Details — {material['name']}");self.resize(860,760)
        root=QVBoxLayout(self);tabs=QTabWidget();root.addWidget(tabs,1)

        detail_rows=db.query('SELECT * FROM material_details WHERE material_id=?',(self.material_id,));detail=dict(detail_rows[0]) if detail_rows else {}

        basic=QWidget();form=QFormLayout(basic)
        self.name=QLineEdit(material['name'] or '');self.cas=QLineEdit(material['cas'] or '')
        self.pred=QLineEdit((material['predilutions'] if 'predilutions' in material.keys() else '') or f"{float(material['concentration_pct'] or 100):g}")
        self.type=QComboBox();self.type.addItems(['raw','natural','dilution','base']);self.type.setCurrentText(material['material_type'] or 'raw')
        self.supplier=QLineEdit(material['supplier'] or '');self.price=QDoubleSpinBox();self.price.setRange(0,1e12);self.price.setDecimals(6);self.price.setValue(float(material['purchase_price'] or 0))
        self.gram=QDoubleSpinBox();self.gram.setRange(0,1e12);self.gram.setDecimals(6);self.gram.setValue(float(material['stock_g'] or 0))
        self.price_per_g=QLabel('')
        self.solvent=QLineEdit(material['solvent'] or '');self.density=QDoubleSpinBox();self.density.setRange(0,20);self.density.setDecimals(6);self.density.setSpecialValueText('');self.density.setValue(float(material['density'] or 0))
        self.density_hint=QLabel('');self.density_hint.setWordWrap(True)
        self.note_position=QComboBox();self.note_position.addItems(['','Top','Mid','Base'])
        saved_position=(detail.get('note_position') or '').strip()
        assigned=db.query('SELECT ng.id,ng.name,ng.color_hex,ng.position FROM material_note_groups mng JOIN note_groups ng ON ng.id=mng.note_group_id WHERE mng.material_id=? ORDER BY ng.sort_order,ng.name COLLATE NOCASE',(self.material_id,))
        if not saved_position and assigned:
            saved_position=(assigned[0]['position'] or '').strip()
        self.note_position.setCurrentText(saved_position)
        self.scent_group=QComboBox();self.scent_group.addItem('(none)',None)
        self._reload_scent_groups(select_id=(int(assigned[0]['id']) if assigned else None))
        scent_row=QWidget();scent_lay=QHBoxLayout(scent_row);scent_lay.setContentsMargins(0,0,0,0);scent_lay.addWidget(self.scent_group,1)
        add_scent=QPushButton('Add Scent Group');add_scent.clicked.connect(self.add_scent_group);scent_lay.addWidget(add_scent)
        self.notes=QTextEdit();self.notes.setMaximumHeight(72);self.notes.setPlainText(material['notes'] or '')
        self._odor_notes_original=detail.get('odor_notes') or ''
        for label,w in [('Name',self.name),('CAS No.',self.cas),('Predilution %',self.pred),('Type',self.type),('Supplier',self.supplier),('Price',self.price),('Gram',self.gram),('Price / gram',self.price_per_g),('Top / Mid / Base',self.note_position),('Scent Group',scent_row),('Notes',self.notes),('Solvent',self.solvent),('Density g/mL',self.density),('Density source',self.density_hint)]:form.addRow(label,w)
        self.price.valueChanged.connect(self._recalc_price);self.gram.valueChanged.connect(self._recalc_price);self._recalc_price();tabs.addTab(basic,'Details')

        ifra_tab=QWidget();iv=QVBoxLayout(ifra_tab);self.ifra_text=QTextEdit();self.ifra_text.setReadOnly(True);iv.addWidget(self.ifra_text,1)
        ib=QPushButton('Refresh from bundled IFRA 51');ib.clicked.connect(self.refresh_ifra);iv.addWidget(ib);tabs.addTab(ifra_tab,'IFRA')

        pub_tab=QWidget();pv=QVBoxLayout(pub_tab);self.pubchem_text=QTextEdit();self.pubchem_text.setReadOnly(True);pv.addWidget(self.pubchem_text,1)
        pb=QPushButton('Refresh PubChem + Density');pb.clicked.connect(self.refresh_pubchem);pv.addWidget(pb);tabs.addTab(pub_tab,'PubChem')

        comp_tab=QWidget();cv=QVBoxLayout(comp_tab);self.comptox_text=QTextEdit();self.comptox_text.setReadOnly(True);cv.addWidget(self.comptox_text,1)
        cb=QPushButton('Refresh CompTox Density');cb.clicked.connect(self.refresh_comptox);cv.addWidget(cb);tabs.addTab(comp_tab,'CompTox')

        buttons=QDialogButtonBox(QDialogButtonBox.Save|QDialogButtonBox.Cancel);buttons.accepted.connect(self._save_and_accept);buttons.rejected.connect(self.reject);root.addWidget(buttons)
        self.refresh_ifra();self._show_cached_pubchem(detail);self._show_cached_comptox(detail)

    def _recalc_price(self):
        g=self.gram.value();self.price_per_g.setText(f'{self.price.value()/g:g}' if g>0 else '—')

    def refresh_ifra(self):
        self._ifra=ifra_details_for_cas(self.db,self.cas.text());self.ifra_text.setPlainText(self._ifra['summary'])

    def _reload_scent_groups(self, select_id=None):
        if not hasattr(self,'scent_group'):
            return
        current=select_id if select_id is not None else self.scent_group.currentData()
        self.scent_group.blockSignals(True)
        self.scent_group.clear();self.scent_group.addItem('(none)',None)
        for row in self.db.query('SELECT id,name,color_hex FROM note_groups ORDER BY sort_order,name COLLATE NOCASE'):
            self.scent_group.addItem(row['name'],int(row['id']))
            idx=self.scent_group.count()-1
            self.scent_group.setItemData(idx,QBrush(QColor(row['color_hex'] or '#d9d9d9')),Qt.BackgroundRole)
        idx=self.scent_group.findData(current)
        self.scent_group.setCurrentIndex(idx if idx>=0 else 0)
        self.scent_group.blockSignals(False)

    def add_scent_group(self):
        dlg=NoteGroupDialog(self)
        if dlg.exec()!=QDialog.Accepted:return
        name,color=dlg.values()
        try:
            gid=self.db.execute('INSERT INTO note_groups(name,position,color_hex) VALUES(?,?,?)',(name,'',color))
            self._reload_scent_groups(int(gid))
        except Exception as e:QMessageBox.warning(self,'Scent Group',str(e))

    def _show_cached_pubchem(self, detail):
        if not detail or not detail.get('pubchem_cid'):
            self.pubchem_text.setPlainText('No cached PubChem data. Click “Refresh PubChem + Density”.');return
        import json
        try:syn=json.loads(detail.get('pubchem_synonyms') or '[]')
        except Exception:syn=[]
        lines=[f"CID: {detail.get('pubchem_cid') or ''}",f"Title: {detail.get('pubchem_title') or ''}",f"IUPAC: {detail.get('pubchem_iupac_name') or ''}",f"Formula: {detail.get('molecular_formula') or ''}",f"Molecular weight: {detail.get('molecular_weight') or ''}",f"InChIKey: {detail.get('inchikey') or ''}",f"Canonical SMILES: {detail.get('canonical_smiles') or ''}",f"Isomeric SMILES: {detail.get('isomeric_smiles') or ''}",f"Fetched: {detail.get('pubchem_fetched_at') or ''}",'',detail.get('pubchem_description') or '']
        density=detail.get('pubchem_density');density_raw=detail.get('pubchem_density_raw') or ''
        if density:
            lines.extend(['',f'Preferred experimental density: {float(density):g} g/mL',density_raw]);self.density_hint.setText(f'PubChem experimental: {float(density):g} g/mL' + (f' — {density_raw}' if density_raw else ''))
        if syn:lines.extend(['','Synonyms:',', '.join(syn[:80])])
        self.pubchem_text.setPlainText('\n'.join(lines))

    def _show_cached_comptox(self, detail):
        if not detail or not detail.get('comptox_density'):
            self.comptox_text.setPlainText('No cached CompTox density. It is used automatically when PubChem has no usable experimental density.');return
        typ=detail.get('comptox_density_type') or ''
        lines=[f"DTXSID: {detail.get('comptox_dtxsid') or ''}",f"Density: {float(detail.get('comptox_density')):g} g/mL",f"Type: {typ}",f"Fetched: {detail.get('comptox_fetched_at') or ''}",'',detail.get('comptox_density_raw') or '']
        self.comptox_text.setPlainText('\n'.join(lines))
        if not self.density_hint.text().strip():self.density_hint.setText(f'CompTox {typ}: {float(detail.get("comptox_density")):g} g/mL')

    def refresh_comptox(self, quiet=False):
        if not self.cas.text().strip():
            if not quiet:QMessageBox.information(self,'CompTox','Enter or enrich a CAS number first.')
            return None
        try:
            self._comptox=fetch_comptox_density_by_cas(self.cas.text(),self._pubchem)
            typ=self._comptox.get('density_type') or 'unknown';val=self._comptox.get('density_g_ml')
            if val and self.density.value()<=0:self.density.setValue(float(val))
            self.comptox_text.setPlainText(f"DTXSID: {self._comptox.get('dtxsid','')}\nDensity: {float(val):g} g/mL\nType: {typ}\nFetched: {self._comptox.get('fetched_at','')}\n\n{self._comptox.get('raw','')}")
            self.density_hint.setText(f'CompTox {typ}: {float(val):g} g/mL')
            return self._comptox
        except Exception as e:
            if not quiet:QMessageBox.warning(self,'CompTox lookup failed',str(e))
            return None

    def refresh_pubchem(self):
        if not self.cas.text().strip():QMessageBox.information(self,'PubChem','Enter or enrich a CAS number first.');return
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            self._pubchem=fetch_pubchem_by_cas(self.cas.text())
            d={'pubchem_cid':self._pubchem['cid'],'pubchem_title':self._pubchem['title'],'pubchem_iupac_name':self._pubchem['iupac_name'],'molecular_formula':self._pubchem['molecular_formula'],'molecular_weight':self._pubchem['molecular_weight'],'inchikey':self._pubchem['inchikey'],'canonical_smiles':self._pubchem['canonical_smiles'],'isomeric_smiles':self._pubchem['isomeric_smiles'],'pubchem_fetched_at':self._pubchem['fetched_at'],'pubchem_description':self._pubchem['description'],'pubchem_synonyms':__import__('json').dumps(self._pubchem['synonyms'],ensure_ascii=False),'pubchem_density':self._pubchem.get('preferred_density_g_ml'),'pubchem_density_raw':self._pubchem.get('preferred_density_raw','')}
            preferred=self._pubchem.get('preferred_density_g_ml')
            if preferred and self.density.value()<=0:self.density.setValue(float(preferred))
            self._show_cached_pubchem(d)
            if not preferred:
                self.refresh_comptox(quiet=True)
                if self._pubchem.get('density_candidates') and self._comptox is None:
                    vals=', '.join(f"{x['g_ml']:g}" for x in self._pubchem['density_candidates'][:6]);self.density_hint.setText('PubChem density annotations conflict ('+vals+' g/mL), and CompTox did not provide a fallback. Enter density manually.')
        except Exception as e:
            # A PubChem miss should not prevent the EPA CompTox fallback from being useful.
            fallback=self.refresh_comptox(quiet=True)
            if fallback is None:QMessageBox.warning(self,'Reference lookup failed',str(e))
        finally:QApplication.restoreOverrideCursor()

    @staticmethod
    def _pred_values(text):
        vals=[]
        for token in re.split(r'[,;/]+',text or ''):
            token=token.strip().replace('%','')
            if not token:continue
            v=float(token.replace(',','.'))
            if not (0<v<=100):raise ValueError('Predilution values must be >0 and <=100.')
            if not any(abs(v-x)<1e-9 for x in vals):vals.append(v)
        return vals or [100.0]

    def _save_group_assignment(self):
        gid=self.scent_group.currentData() if hasattr(self,'scent_group') else None
        with self.db.connect() as conn:
            conn.execute('DELETE FROM material_note_groups WHERE material_id=?',(self.material_id,))
            if gid is not None:
                conn.execute('INSERT INTO material_note_groups(material_id,note_group_id) VALUES(?,?)',(self.material_id,int(gid)))
        return (self.note_position.currentText() if hasattr(self,'note_position') else '')

    def _save_and_accept(self):
        try:
            name=self.name.text().strip()
            if not name:raise ValueError('Name is required.')
            preds=self._pred_values(self.pred.text());pred_text=', '.join(f'{v:g}' for v in preds);price=self.price.value();gram=self.gram.value();unit=price/gram if gram>0 else 0
            mtype=self.type.currentText() or ('dilution' if preds[0]<99.999 else 'raw')
            self.db.execute("""UPDATE materials SET name=?,cas=?,material_type=?,concentration_pct=?,predilutions=?,solvent=?,density=?,supplier=?,purchase_price=?,unit_cost_per_g=?,stock_g=?,notes=? WHERE id=?""",
                            (name,self.cas.text().strip(),mtype,preds[0],pred_text,self.solvent.text().strip(),self.density.value() or None,self.supplier.text().strip(),price if price>0 else None,unit,gram,self.notes.toPlainText().strip(),self.material_id))
            position=self._save_group_assignment();self.refresh_ifra();save_reference_snapshot(self.db,self.material_id,self._ifra,self._pubchem,position,self._odor_notes_original,self._comptox)
            if self.cas.text().strip():save_material_alias(self.db,name,self.cas.text().strip(),name,'User inventory details')
            self.accept()
        except Exception as e:QMessageBox.critical(self,'Save failed',str(e))


class NoWheelComboBox(QComboBox):
    """Editable combo that ignores the mouse wheel.

    Formulator stock choices can have a verbose popup label (for example ``10% Vanillin``) while
    the closed editor intentionally shows only the clean material name (``Vanillin``).  The clean
    label is applied to the line edit by FormulatorTab without changing the selected item/data.
    """
    def wheelEvent(self, event):
        event.ignore()


class CompactDoubleSpinBox(QDoubleSpinBox):
    """Double spin box that keeps precision available but hides meaningless trailing zeroes."""
    def textFromValue(self, value):
        decimals = max(0, int(self.decimals()))
        text = f"{float(value):.{decimals}f}".rstrip('0').rstrip('.')
        return text or '0'


class CurrentRowTintDelegate(QStyledItemDelegate):
    """Paint a very light tint over the current row without changing cell selection semantics."""
    def __init__(self, view):
        super().__init__(view); self.view=view

    def paint(self, painter, option, index):
        super().paint(painter, option, index)
        if index.row() == self.view.currentRow():
            painter.save()
            painter.fillRect(option.rect, QColor(80, 140, 210, 22))
            painter.restore()


class SpreadsheetTable(QTableWidget):
    """Small Excel-like layer for copy/paste/cut/delete/Ctrl+S.

    Delete/Backspace works from selected indexes (including cells that use a cellWidget), rather
    than only selected QTableWidgetItems. A tab can provide clear_callback for custom behavior.
    """
    def __init__(self, *args, paste_callback=None, save_callback=None, clear_callback=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.paste_callback=paste_callback; self.save_callback=save_callback; self.clear_callback=clear_callback
        self.setItemDelegate(CurrentRowTintDelegate(self))
        self.currentCellChanged.connect(lambda *_: self.viewport().update())

    def keyPressEvent(self,event):
        if event.matches(QKeySequence.Paste):
            if self.paste_callback:self.paste_callback(QApplication.clipboard().text())
            return
        if event.matches(QKeySequence.Copy):self.copy_selection();return
        if event.matches(QKeySequence.Cut):self.copy_selection();self.clear_selection_cells();return
        if event.matches(QKeySequence.Save):
            if self.save_callback:self.save_callback()
            return
        if event.key() in (Qt.Key_Delete,Qt.Key_Backspace):
            self.clear_selection_cells();return
        super().keyPressEvent(event)

    def copy_selection(self):
        ranges=self.selectedRanges()
        if not ranges:return
        rg=ranges[0];lines=[]
        for r in range(rg.topRow(),rg.bottomRow()+1):
            vals=[]
            for c in range(rg.leftColumn(),rg.rightColumn()+1):
                if self.isColumnHidden(c):continue
                widget=self.cellWidget(r,c)
                if isinstance(widget,QComboBox):vals.append(widget.currentText())
                else:
                    item=self.item(r,c);vals.append(item.text() if item else '')
            lines.append('\t'.join(vals))
        QApplication.clipboard().setText('\n'.join(lines))

    def clear_selection_cells(self):
        indexes=list(self.selectedIndexes())
        if self.clear_callback:
            self.clear_callback(indexes);return
        old=self.blockSignals(True)
        try:
            for idx in indexes:
                item=self.item(idx.row(),idx.column())
                if item is not None and item.flags() & Qt.ItemIsEditable:
                    item.setText('')
        finally:self.blockSignals(old)


class EnrichResolutionDialog(QDialog):
    """User-confirmed resolver for ambiguous CAS enrichment failures.

    The primary path ranks name candidates.  A final CAS-search panel lets the user look up an
    exact CAS across every local identity source (and PubChem only if the CAS is absent locally),
    then attach that identity to any unresolved inventory row.
    """
    COL_NAME = 0
    COL_MATCH = 1
    COL_CAS = 2
    COL_NOTES = 3

    def __init__(self, db, unresolved_rows, parent=None):
        super().__init__(parent)
        self.db = db
        self.unresolved_rows = unresolved_rows
        self.combos = []
        self.cas_search_rows = []
        self.setWindowTitle('Resolve CAS Enrichment')
        self.resize(1120, min(820, 360 + max(1, len(unresolved_rows)) * 54))

        lay = QVBoxLayout(self)
        info = QLabel(
            'Choose the identity that matches the material you actually own. '
            'Candidates are ranked algorithmically from local identity/IFRA data; PubChem is used automatically only when local name data is insufficient. '
            'If the name still cannot be resolved, use Search by CAS number at the bottom. '
            'Your final choice is saved as a local alias so the same inventory name resolves automatically next time.'
        )
        info.setWordWrap(True)
        lay.addWidget(info)

        self.table = QTableWidget(len(unresolved_rows), 4)
        self.table.setHorizontalHeaderLabels(['Inventory name', 'Choose identity', 'CAS', 'Source / notes'])
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        hdr = self.table.horizontalHeader()
        hdr.setSectionResizeMode(self.COL_NAME, QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(self.COL_MATCH, QHeaderView.Stretch)
        hdr.setSectionResizeMode(self.COL_CAS, QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(self.COL_NOTES, QHeaderView.Stretch)

        for out_row, row_info in enumerate(unresolved_rows):
            name = row_info['name']
            candidates = get_resolution_candidates(db, name, limit=50)
            name_item = QTableWidgetItem(name)
            name_item.setToolTip(unresolved_identity_reason(name))
            self.table.setItem(out_row, self.COL_NAME, name_item)

            combo = QComboBox()
            combo.setMaxVisibleItems(50)
            combo.addItem('— Choose a match —', None)
            for candidate in candidates:
                combo.addItem(self._candidate_label(candidate), candidate)
            if not candidates:
                combo.clear()
                combo.addItem('No name candidates found — use CAS search below', None)
            self.table.setCellWidget(out_row, self.COL_MATCH, combo)
            self.combos.append((combo, row_info))

            self.table.setItem(out_row, self.COL_CAS, QTableWidgetItem(''))
            self.table.setItem(
                out_row, self.COL_NOTES,
                QTableWidgetItem('Select a candidate to inspect it.' if candidates else 'Name search returned no usable identity.')
            )
            combo.currentIndexChanged.connect(lambda _idx, r=out_row, cb=combo: self._combo_changed(r, cb))

        lay.addWidget(self.table, 1)

        # Final fallback: exact CAS search. This deliberately searches identities by CAS rather
        # than trying to infer chemistry from the inventory name.
        cas_group = QGroupBox('Still unresolved? Search by CAS number')
        cas_lay = QVBoxLayout(cas_group)
        cas_top = QHBoxLayout()
        cas_top.addWidget(QLabel('Apply result to:'))
        self.cas_target = QComboBox()
        for out_row, row_info in enumerate(unresolved_rows):
            self.cas_target.addItem(row_info['name'], out_row)
        cas_top.addWidget(self.cas_target, 1)
        cas_top.addWidget(QLabel('CAS:'))
        self.cas_search = QLineEdit()
        self.cas_search.setPlaceholderText('e.g. 63314-79-4')
        self.cas_search.setClearButtonEnabled(True)
        cas_top.addWidget(self.cas_search, 1)
        self.cas_search_btn = QPushButton('Search CAS')
        cas_top.addWidget(self.cas_search_btn)
        cas_lay.addLayout(cas_top)

        self.cas_results = QTableWidget(0, 3)
        self.cas_results.setHorizontalHeaderLabels(['Identity', 'CAS', 'Source'])
        self.cas_results.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.cas_results.setSelectionMode(QAbstractItemView.SingleSelection)
        self.cas_results.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.cas_results.verticalHeader().setVisible(False)
        rh = self.cas_results.horizontalHeader()
        rh.setSectionResizeMode(0, QHeaderView.Stretch)
        rh.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        rh.setSectionResizeMode(2, QHeaderView.Stretch)
        self.cas_results.setMaximumHeight(170)
        cas_lay.addWidget(self.cas_results)

        cas_bottom = QHBoxLayout()
        self.cas_status = QLabel('CAS search is exact. Local Inventory/alias/IFRA sources are checked first.')
        self.cas_status.setWordWrap(True)
        cas_bottom.addWidget(self.cas_status, 1)
        self.cas_use_btn = QPushButton('Use selected CAS identity')
        self.cas_use_btn.setEnabled(False)
        cas_bottom.addWidget(self.cas_use_btn)
        cas_lay.addLayout(cas_bottom)
        lay.addWidget(cas_group)

        self.cas_search_btn.clicked.connect(self._search_by_cas)
        self.cas_search.returnPressed.connect(self._search_by_cas)
        self.cas_use_btn.clicked.connect(self._use_cas_result)
        self.cas_results.itemSelectionChanged.connect(
            lambda: self.cas_use_btn.setEnabled(self.cas_results.currentRow() >= 0)
        )
        self.cas_results.cellDoubleClicked.connect(lambda *_: self._use_cas_result())
        self.table.currentCellChanged.connect(self._sync_target_from_table)
        if unresolved_rows:
            self.table.selectRow(0)

        buttons = QDialogButtonBox(QDialogButtonBox.Apply | QDialogButtonBox.Cancel)
        apply_btn = buttons.button(QDialogButtonBox.Apply)
        apply_btn.setText('Apply selected matches')
        apply_btn.clicked.connect(self.accept)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)

    @staticmethod
    def _candidate_label(candidate):
        display_name = (candidate.get('display_name') or '').strip()
        principal_name = (candidate.get('principal_name') or '').strip()
        score = candidate.get('score')
        score_text = f"   |   match {float(score) * 100:.0f}%" if isinstance(score, (int, float)) else ''
        if principal_name and principal_name.casefold() != display_name.casefold():
            return f"{display_name} → {principal_name}   |   CAS {candidate['cas']}{score_text}"
        return f"{display_name or principal_name}   |   CAS {candidate['cas']}{score_text}"

    def _combo_changed(self, row, combo):
        candidate = combo.currentData()
        if not candidate:
            self.table.item(row, self.COL_CAS).setText('')
            self.table.item(row, self.COL_NOTES).setText('No identity selected.')
            return
        self.table.item(row, self.COL_CAS).setText(candidate['cas'])
        detail = candidate['source']
        if candidate.get('notes'):
            detail += ' — ' + candidate['notes']
        self.table.item(row, self.COL_NOTES).setText(detail)
        self.table.item(row, self.COL_NOTES).setToolTip(detail)

    def _sync_target_from_table(self, current_row, _current_col, _previous_row, _previous_col):
        if 0 <= current_row < self.cas_target.count():
            idx = self.cas_target.findData(current_row)
            if idx >= 0:
                self.cas_target.setCurrentIndex(idx)

    def _search_by_cas(self):
        cas = self.cas_search.text().strip()
        if not re.fullmatch(r'\d{2,7}-\d{2}-\d', cas):
            self.cas_status.setText('Enter a CAS in the form 63314-79-4.')
            self.cas_results.setRowCount(0)
            self.cas_use_btn.setEnabled(False)
            return
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            rows = search_resolution_candidates_by_cas(self.db, cas, limit=12, allow_online=True)
        finally:
            QApplication.restoreOverrideCursor()
        self.cas_search_rows = rows
        self.cas_results.setRowCount(len(rows))
        for r, candidate in enumerate(rows):
            label = self._candidate_label(candidate).rsplit('   |   CAS ', 1)[0]
            item = QTableWidgetItem(label)
            item.setData(Qt.UserRole, candidate)
            self.cas_results.setItem(r, 0, item)
            self.cas_results.setItem(r, 1, QTableWidgetItem(candidate['cas']))
            detail = candidate['source'] + ((' — ' + candidate['notes']) if candidate.get('notes') else '')
            source_item = QTableWidgetItem(detail)
            source_item.setToolTip(detail)
            self.cas_results.setItem(r, 2, source_item)
        if rows:
            self.cas_results.selectRow(0)
            self.cas_status.setText(f'Found {len(rows)} identity candidate(s) for exact CAS {cas}. Select the correct identity and use it for the target material.')
        else:
            self.cas_status.setText(f'No local or PubChem identity was found for CAS {cas}. Nothing was changed.')
            self.cas_use_btn.setEnabled(False)

    def _use_cas_result(self):
        result_row = self.cas_results.currentRow()
        if result_row < 0:
            return
        item = self.cas_results.item(result_row, 0)
        candidate = item.data(Qt.UserRole) if item else None
        if not candidate:
            return
        target_row = self.cas_target.currentData()
        if target_row is None or not (0 <= int(target_row) < len(self.combos)):
            return
        combo, _row_info = self.combos[int(target_row)]
        # Keep the normal selection/application path: CAS search simply injects the selected
        # identity into that row's dropdown, then the existing Apply button persists the alias.
        existing = -1
        for i in range(combo.count()):
            row = combo.itemData(i)
            if row and row.get('cas') == candidate.get('cas') and row.get('principal_name') == candidate.get('principal_name'):
                existing = i
                break
        if existing < 0:
            combo.addItem(self._candidate_label(candidate), candidate)
            existing = combo.count() - 1
        combo.setEnabled(True)
        combo.setCurrentIndex(existing)
        self.table.selectRow(int(target_row))
        self.cas_status.setText(
            f"Selected {candidate.get('principal_name') or candidate.get('display_name')} for {self.unresolved_rows[int(target_row)]['name']}. "
            'Click Apply selected matches to save it.'
        )

    def selections(self):
        selected = []
        for combo, row_info in self.combos:
            candidate = combo.currentData()
            if candidate:
                selected.append((row_info, candidate))
        return selected


class InventoryTab(QWidget):
    COL_ID = 0
    COL_GROUP = 1
    COL_POSITION = COL_GROUP
    COL_NAME = 2
    COL_PRED = 3
    COL_PRICE = 4
    COL_GRAM = 5
    COL_UNIT = 6
    COL_NOTES = 7
    COL_CAS = 8
    COL_SUPPLIER = 9
    COL_TYPE = 10
    COL_SOLVENT = 11

    HEADERS = ['ID', 'Group', 'Name', 'Predilution %', 'Price', 'Gram', 'Price / gram', 'Notes', 'CAS No.', 'Supplier', 'Type', 'Solvent']

    def __init__(self, db, changed_callback=None):
        super().__init__()
        self.db = db
        self.changed_callback = changed_callback
        self._loading = False
        self._updating = False
        self._dirty = False

        lay = QVBoxLayout(self)
        top = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText('Search inventory…')
        self.search.textChanged.connect(self.apply_filter)
        top.addWidget(self.search, 1)

        actions = [
            ('Paste List', self.paste_button),
            ('Resolve Enrich Error', self.resolve_enrich_error),
            ('+ Row', self.add_blank_row),
            ('Delete Row(s)', self.delete_rows),
        ]
        for text, fn in actions:
            b = QPushButton(text)
            b.clicked.connect(fn)
            top.addWidget(b)
        lay.addLayout(top)

        self.help = QLabel(
            'Paste one material per line or paste a table from Excel. '
            'Examples: “Evernyl 10%”, “Ambroxan (20% DPG)”. '
            'CAS is resolved automatically, offline, from the bundled fragrance identity database + official IFRA 51 data when a Name is entered. '
            'Resolve Enrich Error ranks the closest identities automatically; if local data is insufficient it queries PubChem in the background, so you never search databases manually. '
            'Price / gram is calculated automatically and every edit auto-saves. Click a cell and type, or use Ctrl+V, Ctrl+C and Delete.'
        )
        self.help.setWordWrap(True)
        lay.addWidget(self.help)

        self.table = SpreadsheetTable(0, len(self.HEADERS), paste_callback=self.paste_text, save_callback=lambda:self.save_all(silent=True))
        self.table.setHorizontalHeaderLabels(self.HEADERS)
        self.table.setColumnHidden(self.COL_ID, True)
        self.table.setColumnHidden(self.COL_TYPE, True)
        self.table.setColumnHidden(self.COL_SOLVENT, True)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.table.setSelectionBehavior(QAbstractItemView.SelectItems)
        self.table.setEditTriggers(
            QAbstractItemView.AnyKeyPressed |
            QAbstractItemView.DoubleClicked |
            QAbstractItemView.EditKeyPressed |
            QAbstractItemView.SelectedClicked
        )
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setDefaultSectionSize(25)
        header = self.table.horizontalHeader()
        # Excel-like manual column resizing. Stretch/ResizeToContents modes prevent
        # the user from freely dragging the section boundaries.
        for c in range(len(self.HEADERS)):
            header.setSectionResizeMode(c, QHeaderView.Interactive)
        header.setStretchLastSection(False)
        self.table.setColumnWidth(self.COL_POSITION, 150)
        self.table.setColumnWidth(self.COL_NAME, 280)
        self.table.setColumnWidth(self.COL_PRED, 110)
        self.table.setColumnWidth(self.COL_PRICE, 100)
        self.table.setColumnWidth(self.COL_GRAM, 100)
        self.table.setColumnWidth(self.COL_UNIT, 110)
        self.table.setColumnWidth(self.COL_NOTES, 260)
        self.table.setColumnWidth(self.COL_CAS, 150)
        self.table.setColumnWidth(self.COL_SUPPLIER, 190)
        self.table.itemChanged.connect(self.on_item_changed)
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self.show_context_menu)
        lay.addWidget(self.table)

        bottom = QHBoxLayout()
        self.status = QLabel()
        bottom.addWidget(self.status)
        bottom.addStretch()
        lay.addLayout(bottom)
        self.autosave_timer=QTimer(self);self.autosave_timer.setSingleShot(True);self.autosave_timer.setInterval(550);self.autosave_timer.timeout.connect(lambda:self.save_all(silent=True))
        # Notify Formulator only after the save stack has unwound. Refreshing its material
        # combos synchronously from an Inventory itemChanged/autosave callback can invalidate
        # editors/widgets that Qt is still processing.
        self.notify_timer=QTimer(self);self.notify_timer.setSingleShot(True);self.notify_timer.setInterval(80);self.notify_timer.timeout.connect(self._notify_materials_changed)
        self.refresh()

    @staticmethod
    def _float_text(value):
        if value is None:
            return ''
        try:
            v = float(value)
            if abs(v) < 1e-15:
                return ''
            return f'{v:g}'
        except Exception:
            return str(value)

    @staticmethod
    def _parse_predilutions(value):
        """Return unique 0-100 concentration options in the user's entered order."""
        if isinstance(value, (list, tuple)):
            raw_parts = list(value)
        else:
            text = '' if value is None else str(value).strip()
            if not text:
                return [100.0]
            raw_parts = re.split(r'[,;/]+', text)
        out=[]
        for raw in raw_parts:
            token=str(raw).strip().replace('%','')
            if not token:
                continue
            token=token.replace(',','.')
            try:v=float(token)
            except Exception:raise ValueError(f'Invalid predilution: {raw}')
            if not (0 < v <= 100):
                raise ValueError('Predilution values must be > 0 and <= 100')
            if not any(abs(v-x)<1e-9 for x in out):out.append(v)
        return out or [100.0]

    @classmethod
    def _pred_text(cls, value):
        try:
            vals=cls._parse_predilutions(value)
            if len(vals)==1 and abs(vals[0]-100.0)<1e-9:
                return ''
            return ', '.join(f'{v:g}' for v in vals)
        except Exception:
            return '' if value is None else str(value)

    @staticmethod
    def _cas_tokens(value):
        return set(re.findall(r'\b\d{2,7}-\d{2}-\d\b', str(value or '')))

    @staticmethod
    def _parse_number(text, default=0.0):
        s = (text or '').strip()
        if not s:
            return default
        s = re.sub(r'[^0-9,\.\-+Ee]', '', s)
        if ',' in s and '.' in s:
            s = s.replace(',', '')
        elif ',' in s and '.' not in s:
            s = s.replace(',', '.')
        try:
            return float(s)
        except Exception:
            raise ValueError(f'Not a number: {text}')

    def _set_item(self, row, col, value='', editable=True, tooltip=''):
        item = QTableWidgetItem('' if value is None else str(value))
        if not editable:
            item.setFlags(item.flags() & ~Qt.ItemIsEditable)
        if tooltip:
            item.setToolTip(tooltip)
        self.table.setItem(row, col, item)
        return item

    def append_row(self, *, material_id=None, position='', position_color='', position_tip='', name='', pred=100, price=None, gram=None, unit=None,
                   notes='', cas='', supplier='', material_type='raw', solvent='', at_row=None):
        r = self.table.rowCount() if at_row is None else max(0, min(int(at_row), self.table.rowCount()))
        self.table.insertRow(r)
        self._set_item(r, self.COL_ID, '' if material_id is None else material_id, editable=False)
        pos_item=self._set_item(r, self.COL_POSITION, position, editable=False, tooltip=position_tip)
        if position_color:
            pos_item.setBackground(QBrush(QColor(position_color)))
        self._set_item(r, self.COL_NAME, name)
        self._set_item(r, self.COL_PRED, self._pred_text(pred if pred is not None else 100))
        self._set_item(r, self.COL_PRICE, self._float_text(price))
        self._set_item(r, self.COL_GRAM, self._float_text(gram))
        self._set_item(r, self.COL_UNIT, self._float_text(unit), editable=False)
        self._set_item(r, self.COL_NOTES, notes or '')
        self._set_item(r, self.COL_CAS, cas)
        self._set_item(r, self.COL_SUPPLIER, supplier)
        self._set_item(r, self.COL_TYPE, material_type or 'raw', editable=False)
        self._set_item(r, self.COL_SOLVENT, solvent or '', editable=False)
        self.recalculate_row(r)
        return r

    def refresh(self):
        self._loading = True
        self.table.blockSignals(True)
        try:
            self.table.setRowCount(0)
            group_map={}
            for row in self.db.query("""SELECT mng.material_id,ng.name,ng.position,ng.color_hex
                                        FROM material_note_groups mng JOIN note_groups ng ON ng.id=mng.note_group_id
                                        ORDER BY ng.sort_order,ng.name COLLATE NOCASE"""):
                group_map.setdefault(int(row['material_id']),[]).append(dict(row))
            detail_pos={int(r['material_id']):r['note_position'] for r in self.db.query("SELECT material_id,note_position FROM material_details WHERE TRIM(COALESCE(note_position,''))<>''")}
            for m in self.db.list_materials():
                price = m['purchase_price']
                if price is None and float(m['unit_cost_per_g'] or 0) and float(m['stock_g'] or 0):
                    price = float(m['unit_cost_per_g']) * float(m['stock_g'])
                groups=group_map.get(int(m['id']),[])
                group=groups[0] if groups else None
                position=(detail_pos.get(int(m['id']),'') or '').strip()
                # Backward-compatible fallback for groups created in v0.9.8 where the
                # position lived on the group instead of the material detail.
                if not position and group:
                    position=(group.get('position') or '').strip()
                group_name=(group.get('name') or '').strip() if group else ''
                compact=' · '.join(x for x in (position,group_name) if x)
                color=(group.get('color_hex') if group else '') or ''
                tip='\n'.join(x for x in (f'Position: {position}' if position else '',f'Scent group: {group_name}' if group_name else '') if x)
                self.append_row(
                    material_id=m['id'], position=compact,position_color=color,position_tip=tip,
                    name=m['name'], pred=(m['predilutions'] if 'predilutions' in m.keys() and m['predilutions'] else m['concentration_pct']), price=price,
                    gram=m['stock_g'], unit=m['unit_cost_per_g'], notes=m['notes'] or '', cas=m['cas'] or '',
                    supplier=m['supplier'] or '', material_type=m['material_type'], solvent=m['solvent'] or ''
                )
        finally:
            self.table.blockSignals(False)
            self._loading = False
            self._dirty = False
        self.apply_filter()
        self.update_status()

    def _notify_materials_changed(self):
        if not self.changed_callback:
            return
        try:
            self.changed_callback()
        except Exception:
            logging.exception('Inventory post-save material refresh failed')
            self.update_status('material refresh deferred after an error; see PerfumeStudio.log')

    def update_status(self, extra=''):
        visible = 0
        total = 0
        for r in range(self.table.rowCount()):
            item = self.table.item(r, self.COL_NAME)
            has_name = bool(item and item.text().strip())
            if has_name:
                total += 1
                if not self.table.isRowHidden(r):
                    visible += 1
        loaded = self.db.query('SELECT COUNT(*) n FROM ifra_standards')[0]['n']
        aliases = self.db.query("SELECT COUNT(*) n FROM material_aliases WHERE source LIKE 'Bundled:%'")[0]['n']
        dirty = ' • saving…' if self._dirty else ' • saved'
        tail = f' • {extra}' if extra else ''
        self.status.setText(f'{visible}/{total} materials shown • offline identity aliases {aliases} • IFRA 51 standards {loaded}{dirty}{tail}')

    def apply_filter(self):
        q = normalize_material_name(self.search.text())
        for r in range(self.table.rowCount()):
            values = []
            for c in (self.COL_POSITION, self.COL_NAME, self.COL_NOTES, self.COL_CAS, self.COL_SUPPLIER):
                item = self.table.item(r, c)
                values.append(item.text() if item else '')
            hay = normalize_material_name(' '.join(values))
            self.table.setRowHidden(r, bool(q and q not in hay))
        self.update_status()

    def add_blank_row(self):
        current = self.table.currentRow()
        insert_at = current + 1 if current >= 0 else self.table.rowCount()
        r = self.append_row(pred=100, at_row=insert_at)
        self.table.setCurrentCell(r, self.COL_NAME)
        self._dirty = True
        self.schedule_autosave();self.update_status()

    def paste_button(self):
        self.paste_text(QApplication.clipboard().text())

    @staticmethod
    def _header_key(text):
        raw = (text or '').strip().lower().replace(' ', '')
        aliases = {
            'name': 'name', 'material': 'name', 'ingredient': 'name', 'materialname': 'name',
            'predilution': 'pred', 'predilution%': 'pred', 'concentration': 'pred', 'concentration%': 'pred', 'conc': 'pred', 'conc%': 'pred',
            'price': 'price', 'cost': 'price', 'purchaseprice': 'price',
            'gram': 'gram', 'grams': 'gram', 'g': 'gram', 'weight': 'gram', 'weightg': 'gram',
            'price/gram': 'unit', 'pricepergram': 'unit', 'cost/g': 'unit', 'costperg': 'unit',
            'cas': 'cas', 'casno': 'cas', 'casnumber': 'cas', 'casno.': 'cas',
            'supplier': 'supplier', 'vendor': 'supplier',
            'notes': 'notes', 'note': 'notes', 'description': 'notes',
        }
        return aliases.get(raw)

    def _first_paste_row(self):
        current = self.table.currentRow()
        if current >= 0:
            name = self.table.item(current, self.COL_NAME)
            if not name or not name.text().strip():
                return current
        return self.table.rowCount()

    def paste_text(self, text):
        text = (text or '').replace('\r\n', '\n').replace('\r', '\n').strip('\n')
        if not text.strip():
            return
        rows = [line.split('\t') for line in text.split('\n') if line.strip()]
        if not rows:
            return

        self.table.blockSignals(True)
        self._loading = True
        added_rows = []
        try:
            if max(len(x) for x in rows) == 1:
                target = self._first_paste_row()
                for cells in rows:
                    parsed = parse_inventory_line(cells[0])
                    if not parsed.name:
                        continue
                    while target >= self.table.rowCount():
                        self.append_row(pred=100)
                    self.table.item(target, self.COL_NAME).setText(parsed.name)
                    self.table.item(target, self.COL_PRED).setText(self._pred_text(parsed.predilution_pct))
                    if parsed.cas:
                        self.table.item(target, self.COL_CAS).setText(parsed.cas)
                    self.table.item(target, self.COL_SOLVENT).setText(parsed.solvent)
                    if parsed.predilution_pct < 99.999:
                        self.table.item(target, self.COL_TYPE).setText('dilution')
                    self.recalculate_row(target)
                    added_rows.append(target)
                    target += 1
            else:
                keys = [self._header_key(x) for x in rows[0]]
                recognized = sum(k is not None for k in keys)
                has_header = recognized >= 2
                data_rows = rows[1:] if has_header else rows
                target = self._first_paste_row()
                start_col = self.table.currentColumn()
                if start_col < self.COL_NAME or start_col > self.COL_SUPPLIER:
                    start_col = self.COL_NAME
                for cells in data_rows:
                    while target >= self.table.rowCount():
                        self.append_row(pred=100)
                    if has_header:
                        for i, value in enumerate(cells):
                            key = keys[i] if i < len(keys) else None
                            col = {'name': self.COL_NAME, 'pred': self.COL_PRED, 'price': self.COL_PRICE,
                                   'gram': self.COL_GRAM, 'unit': self.COL_UNIT, 'notes': self.COL_NOTES, 'cas': self.COL_CAS,
                                   'supplier': self.COL_SUPPLIER}.get(key)
                            if col is None or col == self.COL_UNIT:
                                continue
                            self.table.item(target, col).setText(value.strip())
                    else:
                        # If the user copied rows from this spreadsheet (or prepared the
                        # same seven visible columns in Excel) there is no header, but the
                        # read-only Price/gram column still occupies a clipboard field.
                        # Consume that field so CAS/Supplier do not shift left.
                        visible_cols = [
                            self.COL_NAME, self.COL_PRED, self.COL_PRICE, self.COL_GRAM,
                            self.COL_UNIT, self.COL_NOTES, self.COL_CAS, self.COL_SUPPLIER,
                        ]
                        if start_col == self.COL_NAME and len(cells) >= 6:
                            for i, value in enumerate(cells[:len(visible_cols)]):
                                col = visible_cols[i]
                                if col == self.COL_UNIT:
                                    continue
                                self.table.item(target, col).setText(value.strip())
                        else:
                            col = start_col
                            for value in cells:
                                while col in (self.COL_POSITION, self.COL_UNIT, self.COL_TYPE, self.COL_SOLVENT):
                                    col += 1
                                if col > self.COL_SUPPLIER:
                                    break
                                self.table.item(target, col).setText(value.strip())
                                col += 1
                    name_item = self.table.item(target, self.COL_NAME)
                    parsed = parse_inventory_line(name_item.text() if name_item else '')
                    if parsed.name:
                        name_item.setText(parsed.name)
                    pred_item = self.table.item(target, self.COL_PRED)
                    if parsed.predilution_pct != 100 and (not pred_item.text().strip() or pred_item.text().strip() == '100'):
                        pred_item.setText(self._float_text(parsed.predilution_pct))
                    if parsed.cas and not self.table.item(target, self.COL_CAS).text().strip():
                        self.table.item(target, self.COL_CAS).setText(parsed.cas)
                    if parsed.solvent:
                        self.table.item(target, self.COL_SOLVENT).setText(parsed.solvent)
                    try:
                        if self._parse_predilutions(pred_item.text())[0] < 99.999 or len(self._parse_predilutions(pred_item.text())) > 1:
                            self.table.item(target, self.COL_TYPE).setText('dilution')
                    except Exception:
                        pass
                    self.recalculate_row(target)
                    added_rows.append(target)
                    target += 1
        finally:
            self._loading = False
            self.table.blockSignals(False)

        self._dirty = True
        enriched = self.enrich_rows(added_rows, quiet=True)
        merged = self._merge_duplicate_cas_rows()
        self.schedule_autosave()
        self.apply_filter()
        self.update_status(f'pasted {len(added_rows)} rows; enriched {enriched} CAS; skipped {merged} duplicate CAS row(s)')

    def recalculate_row(self, row):
        if row < 0 or row >= self.table.rowCount():
            return
        try:
            price_item = self.table.item(row, self.COL_PRICE)
            gram_item = self.table.item(row, self.COL_GRAM)
            unit_item = self.table.item(row, self.COL_UNIT)
            if not price_item or not gram_item or not unit_item:
                return
            price = self._parse_number(price_item.text(), 0)
            gram = self._parse_number(gram_item.text(), 0)
            unit = price / gram if gram > 0 else 0
            old = self.table.blockSignals(True)
            try:
                unit_item.setText(self._float_text(unit))
            finally:
                self.table.blockSignals(old)
        except Exception:
            old = self.table.blockSignals(True)
            try:
                if self.table.item(row, self.COL_UNIT):
                    self.table.item(row, self.COL_UNIT).setText('')
            finally:
                self.table.blockSignals(old)

    def on_item_changed(self, item):
        if self._loading or self._updating:
            return
        try:
            self._dirty = True
            row, col = item.row(), item.column()
            if row < 0 or row >= self.table.rowCount():
                return
            if col == self.COL_NAME:
                cas_item = self.table.item(row, self.COL_CAS)
                if cas_item and cas_item.data(Qt.UserRole) in ('auto-ifra', 'auto-inventory', 'auto-resolved', 'auto-alias', 'auto-local'):
                    self._updating = True
                    try:
                        cas_item.setText(''); cas_item.setToolTip(''); cas_item.setData(Qt.UserRole, None)
                    finally:
                        self._updating = False
                parsed = parse_inventory_line(item.text())
                if parsed.name and (parsed.name != item.text() or parsed.predilution_pct != 100):
                    self._updating = True
                    try:
                        item.setText(parsed.name)
                        if parsed.predilution_pct != 100:
                            self.table.item(row, self.COL_PRED).setText(self._pred_text(parsed.predilution_pct))
                            self.table.item(row, self.COL_TYPE).setText('dilution')
                        if parsed.cas and not self.table.item(row, self.COL_CAS).text().strip():
                            self.table.item(row, self.COL_CAS).setText(parsed.cas)
                            self.table.item(row, self.COL_CAS).setData(Qt.UserRole, None)
                        if parsed.solvent:
                            self.table.item(row, self.COL_SOLVENT).setText(parsed.solvent)
                    finally:
                        self._updating = False
                self.enrich_rows([row], quiet=True)
            elif col == self.COL_CAS:
                if item.data(Qt.UserRole) is not None:
                    self._updating = True
                    try:item.setData(Qt.UserRole, None)
                    finally:self._updating = False
            if col in (self.COL_PRICE, self.COL_GRAM):
                self.recalculate_row(row)
            self.schedule_autosave()
            self.apply_filter()
        except Exception:
            # A bad pasted/intermediate value should never take the Qt event loop down.
            logging.exception('Inventory item update failed at row=%s col=%s', getattr(item,'row',lambda:-1)(), getattr(item,'column',lambda:-1)())
            self.update_status('update error contained; see PerfumeStudio.log')

    def schedule_autosave(self):
        if self._loading or self._updating:return
        self._dirty=True;self.autosave_timer.start()
        self.update_status()

    def _selected_rows(self):
        return sorted({idx.row() for idx in self.table.selectedIndexes()})

    def _merge_duplicate_cas_rows(self):
        """Merge newly-added rows into an existing row when their CAS overlaps.

        Existing Inventory rows are never silently collapsed. This only prevents a fresh paste/new
        unsaved row from creating another material for a CAS already represented in the table.
        Predilution options are unioned in the user's existing order.
        """
        keeper_for_cas={}
        remove=[]
        for r in range(self.table.rowCount()):
            cas_item=self.table.item(r,self.COL_CAS)
            tokens=self._cas_tokens(cas_item.text() if cas_item else '')
            if not tokens:
                continue
            mid=(self.table.item(r,self.COL_ID).text().strip() if self.table.item(r,self.COL_ID) else '')
            target=None
            for cas in tokens:
                if cas in keeper_for_cas:
                    candidate=keeper_for_cas[cas]
                    candidate_mid=(self.table.item(candidate,self.COL_ID).text().strip() if self.table.item(candidate,self.COL_ID) else '')
                    # Only suppress the current row if it is unsaved. Preserve pre-existing duplicate DB rows.
                    if not mid:
                        target=candidate
                        break
            if target is None:
                for cas in tokens:
                    keeper_for_cas.setdefault(cas,r)
                continue

            # Merge predilutions and fill only blank metadata on the keeper.
            try:
                existing=self._parse_predilutions(self.table.item(target,self.COL_PRED).text())
                incoming=self._parse_predilutions(self.table.item(r,self.COL_PRED).text())
                merged=list(existing)
                for v in incoming:
                    if not any(abs(v-x)<1e-9 for x in merged):merged.append(v)
                self.table.item(target,self.COL_PRED).setText(self._pred_text(merged))
            except Exception:
                pass
            for col in (self.COL_PRICE,self.COL_GRAM,self.COL_NOTES,self.COL_SUPPLIER):
                dst=self.table.item(target,col);src=self.table.item(r,col)
                if dst is not None and src is not None and not dst.text().strip() and src.text().strip():
                    dst.setText(src.text())
            remove.append(r)

        for r in reversed(remove):
            self.table.removeRow(r)
        return len(remove)

    def resolve_enrich_error(self):
        """Resolve enrichment failures with ranked, algorithmic candidate suggestions.

        High-confidence local matches are retried first. Remaining names are normalized and
        fuzzily searched across local IFRA/identity data; PubChem name services are an automatic
        fallback and are cached. No candidate is applied without the user's explicit choice.
        """
        selected = self._selected_rows()
        if selected:
            rows = [r for r in selected if self.table.item(r, self.COL_NAME)
                    and self.table.item(r, self.COL_NAME).text().strip()
                    and not self.table.item(r, self.COL_CAS).text().strip()]
        else:
            rows = [r for r in range(self.table.rowCount()) if self.table.item(r, self.COL_NAME)
                    and self.table.item(r, self.COL_NAME).text().strip()
                    and not self.table.item(r, self.COL_CAS).text().strip()]
        if not rows:
            QMessageBox.information(self, 'Resolve Enrich Error', 'No unresolved CAS cells in the selected rows.')
            return

        # First retry all safe automatic resolvers. The dialog is only for genuinely
        # ambiguous/not-quite-matching rows.
        auto_count = self.enrich_rows(rows, quiet=True)
        unresolved = []
        for r in rows:
            name_item = self.table.item(r, self.COL_NAME)
            cas_item = self.table.item(r, self.COL_CAS)
            if name_item and name_item.text().strip() and not (cas_item and cas_item.text().strip()):
                unresolved.append({'table_row': r, 'name': name_item.text().strip()})

        if not unresolved:
            if auto_count:
                self._dirty = True
            self.update_status(f'resolved {auto_count} CAS automatically')
            QMessageBox.information(self, 'Resolve Enrich Error', f'Resolved all {auto_count} CAS automatically.')
            return

        dlg = EnrichResolutionDialog(self.db, unresolved, self)
        if dlg.exec() != QDialog.Accepted:
            self.update_status(f'{auto_count} auto-resolved; {len(unresolved)} still unresolved')
            return

        chosen = dlg.selections()
        applied = 0
        self.table.blockSignals(True)
        try:
            for row_info, candidate in chosen:
                r = row_info['table_row']
                cas_item = self.table.item(r, self.COL_CAS)
                if cas_item is None:
                    cas_item = QTableWidgetItem('')
                    self.table.setItem(r, self.COL_CAS, cas_item)
                cas_item.setText(candidate['cas'])
                tooltip = f"Resolved as {candidate['principal_name']}\nSource: {candidate['source']}"
                if candidate.get('notes'):
                    tooltip += '\n' + candidate['notes']
                cas_item.setToolTip(tooltip)
                cas_item.setData(Qt.UserRole, None)

                # Teach the user's local alias DB. This makes a future row named exactly
                # the same way resolve immediately without reopening this dialog.
                save_material_alias(
                    self.db,
                    row_info['name'],
                    candidate['cas'],
                    principal_name=candidate['principal_name'],
                    source='User resolved from local candidates: ' + candidate['source'],
                    confidence=1.0,
                    notes=candidate.get('notes', ''),
                )
                applied += 1
        finally:
            self.table.blockSignals(False)

        if auto_count or applied:
            self._dirty = True
            self.schedule_autosave()
        still_unresolved = len(unresolved) - applied
        self.update_status(
            f'{auto_count} auto-resolved; {applied} manually resolved; {still_unresolved} unresolved'
        )
        if applied:
            QMessageBox.information(
                self, 'Resolve Enrich Error',
                f'Applied {applied} selected match(es).\n\n'
                'The choices were saved to your local alias database and will be reused automatically.\n'
                f'{still_unresolved} row(s) were left unresolved because no option was selected.'
            )
        elif still_unresolved:
            QMessageBox.information(
                self, 'Resolve Enrich Error',
                'No candidate was selected. Nothing was changed.'
            )

    def enrich_selected_or_all(self):
        rows = self._selected_rows() or list(range(self.table.rowCount()))
        count = self.enrich_rows(rows, quiet=False)
        if count:
            self._dirty = True
        self.update_status(f'enriched {count} CAS')

    def enrich_rows(self, rows, quiet=True):
        nstd = self.db.query('SELECT COUNT(*) n FROM ifra_standards')[0]['n']
        nncs = self.db.query('SELECT COUNT(*) n FROM ncs_contributions')[0]['n']
        index = build_ifra_name_index(self.db) if (nstd or nncs) else {}

        # First build a conservative name -> CAS map from the inventory itself.
        # This makes predilutions inherit the CAS from an already-known neat row.
        inventory_index = {}
        for rr in range(self.table.rowCount()):
            name = self.table.item(rr, self.COL_NAME).text().strip() if self.table.item(rr, self.COL_NAME) else ''
            cas = self.table.item(rr, self.COL_CAS).text().strip() if self.table.item(rr, self.COL_CAS) else ''
            if not name or not cas:
                continue
            for candidate in material_name_candidates(name):
                inventory_index.setdefault(candidate, set()).add(cas)

        count = 0
        self.table.blockSignals(True)
        try:
            for r in rows:
                if r < 0 or r >= self.table.rowCount():
                    continue
                name_item = self.table.item(r, self.COL_NAME)
                cas_item = self.table.item(r, self.COL_CAS)
                if not name_item or not name_item.text().strip() or (cas_item and cas_item.text().strip()):
                    continue
                match = None
                saved = lookup_saved_alias(self.db, name_item.text())
                if saved:
                    match = {
                        'cas': saved['cas'], 'source': f"Saved alias ({saved['source']})", 'match_type': 'saved',
                        'canonical': saved['principal_name'] or name_item.text(), 'score': 1.0,
                    }
                for candidate in material_name_candidates(name_item.text()) if not match else []:
                    cases = inventory_index.get(candidate, set())
                    if len(cases) == 1:
                        match = {
                            'cas': next(iter(cases)), 'source': 'Inventory', 'match_type': 'exact',
                            'canonical': name_item.text(), 'score': 1.0,
                        }
                        break
                if not match:
                    local_candidate=high_confidence_local_candidate(self.db,name_item.text())
                    if local_candidate:
                        match={'cas':local_candidate['cas'],'source':local_candidate['source'],
                               'match_type':'high-confidence local','canonical':local_candidate['principal_name'],
                               'score':local_candidate['score']}
                if not match and index:
                    match = enrich_name_from_ifra(name_item.text(), index)
                if not match:continue
                cas_item.setText(match['cas'])
                cas_item.setData(Qt.UserRole, 'auto-inventory' if match['source'] == 'Inventory' else ('auto-alias' if match['match_type'] == 'saved' else 'auto-ifra'))
                cas_item.setToolTip(
                    f"{match['source']} • {match['match_type']} match • {match['canonical']} • score {match['score']:.3f}"
                )
                if 'NCS' in match['source'] and self.table.item(r, self.COL_TYPE).text() == 'raw':
                    self.table.item(r, self.COL_TYPE).setText('natural')
                count += 1
        finally:
            self.table.blockSignals(False)
        return count

    def ifra_data_changed(self):
        """Called after IFRA import so existing blank CAS cells enrich without a button."""
        count = self.enrich_rows(list(range(self.table.rowCount())), quiet=True)
        if count:
            self._dirty = True
            self.schedule_autosave()
        self.update_status(f'auto-enriched {count} CAS after IFRA import')

    def save_all(self, silent=False):
        # Never mutate/save the backing model while Qt still owns an active cell editor.
        # The editor commits on focus/Enter; then the restarted timer saves the stable value.
        try:
            if self.table.state() == QAbstractItemView.EditingState:
                self.autosave_timer.start()
                return False
        except Exception:
            pass
        errors = []
        saved = 0
        learned_aliases = []
        try:
            with self.db.connect() as conn:
                for r in range(self.table.rowCount()):
                    name_item = self.table.item(r, self.COL_NAME)
                    name = (name_item.text() if name_item else '').strip()
                    if not name:
                        continue
                    try:
                        pred_item=self.table.item(r,self.COL_PRED); pred_values = self._parse_predilutions(pred_item.text() if pred_item else '')
                        pred = pred_values[0]
                        pred_text = ', '.join(f'{v:g}' for v in pred_values)
                        price_item=self.table.item(r,self.COL_PRICE); gram_item=self.table.item(r,self.COL_GRAM)
                        price_text = price_item.text() if price_item else ''
                        gram_text = gram_item.text() if gram_item else ''
                        price = self._parse_number(price_text, 0); gram = self._parse_number(gram_text, 0)
                        if price < 0 or gram < 0: raise ValueError('Price and gram cannot be negative')
                        unit = price / gram if gram > 0 else 0
                        cas_item=self.table.item(r,self.COL_CAS); notes_item=self.table.item(r,self.COL_NOTES); supplier_item=self.table.item(r,self.COL_SUPPLIER)
                        type_item=self.table.item(r,self.COL_TYPE); solvent_item=self.table.item(r,self.COL_SOLVENT); id_item=self.table.item(r,self.COL_ID)
                        cas = cas_item.text().strip() if cas_item else ''
                        notes = notes_item.text().strip() if notes_item else ''
                        supplier = supplier_item.text().strip() if supplier_item else ''
                        material_type = (type_item.text().strip() if type_item else '') or 'raw'
                        solvent = solvent_item.text().strip() if solvent_item else ''
                        if pred < 99.999 and material_type == 'raw': material_type = 'dilution'
                        mid_text = id_item.text().strip() if id_item else ''
                        if mid_text:
                            conn.execute("""UPDATE materials SET name=?, cas=?, concentration_pct=?, predilutions=?, supplier=?, purchase_price=?,
                                              unit_cost_per_g=?, stock_g=?, material_type=?, solvent=?, notes=? WHERE id=?""",
                                         (name, cas, pred, pred_text, supplier, price if price_text.strip() else None,
                                          unit, gram, material_type, solvent, notes, int(mid_text)))
                        else:
                            cur = conn.execute("""INSERT INTO materials(
                                name,cas,material_type,concentration_pct,predilutions,solvent,supplier,purchase_price,
                                unit_cost_per_g,currency,stock_g,notes
                            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                            (name, cas, material_type, pred, pred_text, solvent, supplier,
                             price if price_text.strip() else None, unit, 'USD', gram, notes))
                            if id_item:id_item.setText(str(cur.lastrowid))
                        saved += 1
                        if cas:learned_aliases.append((name, cas))
                    except Exception as e:
                        errors.append(f'Row {r + 1} ({name or "blank"}): {e}')
                if errors:
                    raise ValueError('\n'.join(errors[:12]) + ('\n…' if len(errors) > 12 else ''))
        except Exception as e:
            logging.exception('Inventory autosave failed')
            self.update_status('autosave waiting for valid cell values; details in PerfumeStudio.log')
            if not silent: QMessageBox.critical(self, 'Save failed', str(e))
            return False

        for alias_name, alias_cas in learned_aliases:
            try:
                existing = lookup_saved_alias(self.db, alias_name)
                if not existing or existing['cas'] != alias_cas or not str(existing['source']).startswith('Bundled:'):
                    save_material_alias(self.db, alias_name, alias_cas, alias_name, 'User inventory')
            except Exception:
                logging.exception('Saving inventory alias failed for %s', alias_name)

        self._dirty = False
        if self.changed_callback:self.notify_timer.start()
        self.update_status(f'auto-saved {saved} materials')
        return True

    def show_context_menu(self, pos):
        idx=self.table.indexAt(pos)
        if not idx.isValid():return
        row=idx.row();self.table.setCurrentCell(row,self.COL_NAME)
        menu=QMenu(self);details=menu.addAction('Details')
        chosen=menu.exec(self.table.viewport().mapToGlobal(pos))
        if chosen==details:self.open_material_details(row)

    def open_material_details(self,row):
        if row<0 or row>=self.table.rowCount():return
        self.save_all(silent=True)
        id_item=self.table.item(row,self.COL_ID);mid=(id_item.text().strip() if id_item else '')
        if not mid:
            self.save_all(silent=True);mid=(self.table.item(row,self.COL_ID).text().strip() if self.table.item(row,self.COL_ID) else '')
        if not mid:return
        material=self.db.material_by_id(int(mid))
        if material is None:return
        dlg=MaterialDetailsDialog(self.db,material,self)
        if dlg.exec()==QDialog.Accepted:
            self.refresh()
            if self.changed_callback:self.notify_timer.start()

    def delete_rows(self):
        rows = self._selected_rows()
        if not rows:
            return
        if QMessageBox.question(self, 'Delete', f'Delete {len(rows)} selected inventory row(s)?') != QMessageBox.Yes:
            return
        try:
            with self.db.connect() as conn:
                for r in rows:
                    mid = self.table.item(r, self.COL_ID).text().strip() if self.table.item(r, self.COL_ID) else ''
                    if mid:
                        conn.execute('DELETE FROM materials WHERE id=?', (int(mid),))
            for r in reversed(rows):
                self.table.removeRow(r)
            if self.changed_callback:self.notify_timer.start()
            self._dirty = False
            self.update_status(f'deleted {len(rows)} rows')
        except Exception as e:
            QMessageBox.critical(self, 'Delete failed', str(e))

class PasteFormulaDialog(QDialog):
    def __init__(self,parent=None,default_name='Pasted Formula'):
        super().__init__(parent)
        self.setWindowTitle('Paste Formula')
        self.resize(720,560)
        lay=QVBoxLayout(self)
        name_row=QHBoxLayout();name_row.addWidget(QLabel('Formula name'))
        self.name=QLineEdit(default_name);name_row.addWidget(self.name,1);lay.addLayout(name_row)
        help_label=QLabel('Paste one material and one parts value per line. Excel/tab-separated text is supported. Values are normalized to 1000 active parts when imported.')
        help_label.setWordWrap(True);lay.addWidget(help_label)
        self.text=QTextEdit();self.text.setPlaceholderText('Iso E Super\t594.97\nEthylene Brassylate\t130.3\nCashmeran\t35.08')
        lay.addWidget(self.text,1)
        buttons=QDialogButtonBox(QDialogButtonBox.Ok|QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText('Import')
        buttons.accepted.connect(self.accept);buttons.rejected.connect(self.reject);lay.addWidget(buttons)


class FormulatorTab(QWidget):
    """Active parts-per-thousand formula editor with optional Inventory manufacturing links.

    Formula identity is preserved independently from Inventory. For an imported material that is
    unavailable, the row keeps a red "Original" choice, a "Don't use" choice, and every Inventory
    material as a possible substitute. Formula parts are always stored with a total of 1000.
    """
    COL_MATERIAL=0;COL_PARTS=1;COL_WEIGHT=2;COL_INV=3;COL_OVERRIDE=4;COL_COST=5
    HEADERS=['Material','Parts / 1000','Weight (mg)','Inventory %','Override %','Cost']
    ORIGINAL_SENTINEL='__FORMULA_ORIGINAL__'
    SKIP_SENTINEL='__FORMULA_SKIP__'
    RED='#d32f2f';ORANGE='#ef6c00';GRAY='#757575'

    def __init__(self, db, data_dir: Path):
        super().__init__();self.db=db;self.data_dir=Path(data_dir);self._loading=False;self._recalculating=False
        self.materials_changed_callback=None;self.formulas_changed_callback=None;self.active_formula_changed_callback=None
        lay=QVBoxLayout(self)

        bar=QHBoxLayout();self.formula=QComboBox();self.formula.currentIndexChanged.connect(self.load_formula)
        buttons=[('New',self.new_formula),('Import Formula',self.import_formula),('Paste Formula',self.paste_formula),
                 ('Export TXT',self.export_formula),('Delete Formula',self.delete_formula),('+ Material',self.add_line),('- Row',self.remove_line)]
        for txt,fn in buttons:
            b=QPushButton(txt);b.clicked.connect(fn);bar.addWidget(b)
        bar.insertWidget(0,QLabel('Formula:'));bar.insertWidget(1,self.formula,1);lay.addLayout(bar)

        hint=QLabel('Formula composition is stored as ACTIVE parts / 1000. Inventory dilution changes only the weighing plan. '
                    'Missing imported materials stay red; choose the original, skip it, or select an Inventory substitute from the Material dropdown.')
        hint.setWordWrap(True);lay.addWidget(hint)

        controls=QHBoxLayout()
        self.batch=CompactDoubleSpinBox();self.batch.setRange(.001,1e9);self.batch.setValue(100);self.batch.setSuffix(' g');self.batch.setDecimals(4)
        self.loadpct=QDoubleSpinBox();self.loadpct.setRange(0,100);self.loadpct.setValue(20);self.loadpct.setSuffix(' %');self.loadpct.setDecimals(3)
        self.batch.valueChanged.connect(self.formula_changed);self.loadpct.valueChanged.connect(self.formula_changed)
        controls.addWidget(QLabel('Batch size'));controls.addWidget(self.batch);controls.addSpacing(12)
        controls.addWidget(QLabel('Finished strength'));controls.addWidget(self.loadpct);controls.addSpacing(22)
        self.current_strength=QLabel('Current strength without solvent: —');controls.addWidget(self.current_strength);controls.addSpacing(18)
        self.solvent_summary=QLabel('Add — g solvent to match strength');controls.addWidget(self.solvent_summary);controls.addSpacing(18)
        self.cost_summary=QLabel('Cost: —');controls.addWidget(self.cost_summary);controls.addStretch();lay.addLayout(controls)

        note_row=QHBoxLayout();note_row.addWidget(QLabel('Note'))
        self.formula_note=QLineEdit();self.formula_note.setPlaceholderText('Optional formula note');self.formula_note.textEdited.connect(lambda _t:self.schedule_autosave())
        note_row.addWidget(self.formula_note,1);lay.addLayout(note_row)

        self.warning=QLabel('');self.warning.setWordWrap(True);self.warning.hide();lay.addWidget(self.warning)

        self.table=SpreadsheetTable(0,len(self.HEADERS),save_callback=lambda:self.save_formula(show_message=False),clear_callback=self.clear_formula_cells)
        self.table.setHorizontalHeaderLabels(self.HEADERS)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection);self.table.setSelectionBehavior(QAbstractItemView.SelectItems)
        self.table.setEditTriggers(QAbstractItemView.AnyKeyPressed|QAbstractItemView.DoubleClicked|QAbstractItemView.EditKeyPressed|QAbstractItemView.SelectedClicked)
        hdr=self.table.horizontalHeader()
        for c in range(len(self.HEADERS)):hdr.setSectionResizeMode(c,QHeaderView.Interactive)
        self.table.setColumnWidth(self.COL_MATERIAL,370);self.table.setColumnWidth(self.COL_PARTS,115);self.table.setColumnWidth(self.COL_WEIGHT,115)
        self.table.setColumnWidth(self.COL_INV,100);self.table.setColumnWidth(self.COL_OVERRIDE,105);self.table.setColumnWidth(self.COL_COST,110)
        hdr.setStretchLastSection(False)
        self.table.itemChanged.connect(self.formula_changed);lay.addWidget(self.table)

        summary_row=QHBoxLayout();self.bottom_summary=QLabel('0 ingredients  |  0 parts  |  Weight before adding solvent: —  |  Dilution before adding solvent: —  |  Total cost: —')
        summary_row.addWidget(self.bottom_summary);summary_row.addStretch();lay.addLayout(summary_row)

        self.autosave_timer=QTimer(self);self.autosave_timer.setSingleShot(True);self.autosave_timer.setInterval(550)
        self.autosave_timer.timeout.connect(lambda:self.save_formula(show_message=False))
        self.refresh_formulas()
        try:sync_all_formula_txt(self.db,self.data_dir)
        except Exception:pass

    @staticmethod
    def _predilution_options(material):
        raw=''
        try:
            if 'predilutions' in material.keys():raw=material['predilutions'] or ''
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

    @staticmethod
    def _fmt_g(value, digits=4):
        if value is None:return '—'
        s=f'{float(value):.{digits}f}'.rstrip('0').rstrip('.')
        return s or '0'

    @staticmethod
    def _fmt_weight_mg(value):
        if value is None:return ''
        mg=float(value)
        if mg>=1.0:return f'{mg:.0f}'
        if mg<=0:return '0'
        return f'{mg:.3f}'.rstrip('0').rstrip('.')

    def _solvent_price_per_g(self):
        try:
            rows=self.db.query("SELECT value FROM app_meta WHERE key='solvent_price_per_g'")
            return max(0.0,float(rows[0]['value'])) if rows else 0.0
        except Exception:
            return 0.0

    @staticmethod
    def _fmt_parts(value):
        """Display-only formatting for active parts / 1000.

        Rules:
        - >= 10 parts: hide the fractional part (e.g. 594.9696 -> 594).
        - < 10 parts: at most two decimal places, trimming trailing zeroes.
        - Values too small to survive two decimal places (< 0.01) use two
          significant figures instead (e.g. 0.005 -> 0.0050).

        This never changes the stored/exported formula value.
        """
        try:
            v=float(value)
        except Exception:
            return ''
        if v == 0:
            return '0'

        av=abs(v)
        if av >= 10:
            # Deliberately hide the fractional part rather than rounding it up.
            # Example: 594.9696 is displayed as 594, while the stored value remains exact.
            return str(int(v))

        if av >= 0.01:
            return f'{v:.2f}'.rstrip('0').rstrip('.')

        # Trace values: two significant figures, avoiding scientific notation for
        # the ranges normally encountered in perfume formulas.
        import math
        decimals=max(0, 1-int(math.floor(math.log10(av))))
        return f'{v:.{decimals}f}'

    def schedule_autosave(self):
        if not self._loading and self.formula.currentData():self.autosave_timer.start()

    def _material_candidates(self,name):
        norm=normalize_material_name(name)
        if not norm:return []
        identity=resolve_local_identity(self.db,name);lookup_cas=(identity['cas'] if identity else '') or ''
        out=[]
        for m in self.db.list_materials():
            same_name=normalize_material_name(m['name'])==norm
            same_cas=bool(lookup_cas and lookup_cas in str(m['cas'] or ''))
            if same_name or same_cas:out.append(m)
        return out

    def _best_material_for_typed_name(self,name):
        candidates=self._material_candidates(name)
        if not candidates:return None
        if len(candidates)==1:return candidates[0]
        def key(m):
            try:stock=float(m['stock_g'] or 0)
            except Exception:stock=0
            conc=self._predilution_options(m)[0]
            return (stock>0,conc,stock)
        return sorted(candidates,key=key,reverse=True)[0]

    @staticmethod
    def _stock_choice(data):
        if isinstance(data, tuple) and len(data)==2:
            try:return int(data[0]),float(data[1])
            except Exception:return None,None
        if isinstance(data,int):return int(data),None
        return None,None

    @staticmethod
    def _find_stock_choice_index(combo, material_id, predilution_pct=None):
        for i in range(combo.count()):
            mid,pct=FormulatorTab._stock_choice(combo.itemData(i))
            if mid != material_id:continue
            if predilution_pct is None or pct is None or abs(float(pct)-float(predilution_pct))<1e-9:
                return i
        return -1

    def _clean_combo_display_text(self, combo):
        """Return the compact text shown while the stock selector is closed.

        Popup entries keep their predilution prefix so a user can choose between multiple stocks,
        but the Formulator Material cell itself only shows the material identity.
        """
        data=combo.currentData()
        if data==self.ORIGINAL_SENTINEL:
            original=str(combo.property('original_name') or '').strip()
            return f'⚠ Original: {original}' if original else combo.currentText()
        if data==self.SKIP_SENTINEL:
            return ''
        mid,_pct=self._stock_choice(data)
        if mid is not None:
            m=self.db.material_by_id(mid)
            if m is not None:return str(m['name'] or '').strip()
        return combo.currentText().strip()

    def _sync_combo_display(self, combo):
        """Show only the material name in the closed editable combo without losing currentData()."""
        if not isinstance(combo,QComboBox) or combo.currentIndex()<0 or combo.lineEdit() is None:return
        text=self._clean_combo_display_text(combo)
        le=combo.lineEdit();old=le.blockSignals(True)
        try:le.setText(text)
        finally:le.blockSignals(old)

    def _material_combo_changed(self, combo):
        """Persist a popup selection without letting editingFinished reinterpret its display text.

        v0.9.3 connected currentIndexChanged directly to formula_changed and also processed the
        editable lineEdit on focus loss.  Selecting e.g. ``10% Vanillin`` was therefore later
        re-read as typed free text, which could replace/reset a substitution when focus or scrolling
        closed the editor.  A real selected item is now authoritative; editingFinished only resolves
        genuinely free-typed text (currentIndex == -1).
        """
        if self._loading:return
        self._sync_combo_display(combo)
        self.recalc()
        # Material/substitution choices are important enough to commit immediately.  This also
        # prevents a fast focus/formula change from cancelling the normal 550 ms autosave timer.
        self.autosave_timer.stop()
        self.save_formula(show_message=False)

    def _material_combo_text_edited(self, combo, text):
        # A user typing over a selected stock is explicitly entering free text.  Detach the index;
        # programmatic setText() used for compact display does not emit textEdited.
        if self._loading:return
        if combo.currentIndex()>=0:
            old=combo.blockSignals(True)
            try:
                combo.setCurrentIndex(-1)
                if combo.lineEdit():combo.lineEdit().setText(text)
            finally:combo.blockSignals(old)

    def _populate_combo(self,combo,material_id=None,material_name='',original_name='',disabled=False,allow_auto_match=False,selected_predilution_pct=None):
        combo.blockSignals(True);combo.clear()
        original=(original_name or material_name or '').strip()
        selected=(material_name or '').strip()
        combo.setProperty('original_name',original)

        chosen=False
        m=self.db.material_by_id(material_id) if material_id is not None else None
        needs_resolution=bool(original and (m is None or normalize_material_name(original)!=normalize_material_name(m['name'])))
        if needs_resolution:
            idx=combo.count();combo.addItem(f'⚠ Original: {original}',self.ORIGINAL_SENTINEL)
            combo.setItemData(idx,QBrush(QColor(self.RED)),Qt.ForegroundRole)
            combo.setItemData(idx,'Imported/original formula material. It is not currently linked to Inventory.',Qt.ToolTipRole)
            idx=combo.count();combo.addItem('',self.SKIP_SENTINEL)
            combo.setItemData(idx,'Leave blank to not use this original material in the current making plan.',Qt.ToolTipRole)

        if not needs_resolution:
            combo.addItem('',None)

        for mat in self.db.list_materials():
            opts=self._predilution_options(mat)
            for pct in opts:
                # Each predilution is a separate manufacturing choice even when stored on one Inventory row.
                combo.addItem(f'{pct:g}% {mat["name"]}',(int(mat['id']),float(pct)));idx=combo.count()-1
                tip=f'Inventory stock: {pct:g}% {mat["name"]}'
                if mat['supplier']:tip+=f" | {mat['supplier']}"
                combo.setItemData(idx,tip,Qt.ToolTipRole)

        if disabled and needs_resolution:
            i=combo.findData(self.SKIP_SENTINEL);combo.setCurrentIndex(i);chosen=True
        elif m is not None:
            opts=self._predilution_options(m)
            preferred=selected_predilution_pct if selected_predilution_pct is not None else opts[0]
            i=self._find_stock_choice_index(combo,int(m['id']),preferred)
            if i<0:i=self._find_stock_choice_index(combo,int(m['id']),opts[0])
            if i>=0:combo.setCurrentIndex(i);chosen=True
        elif needs_resolution:
            i=combo.findData(self.ORIGINAL_SENTINEL);combo.setCurrentIndex(i);chosen=True
        elif allow_auto_match and selected:
            auto=self._best_material_for_typed_name(selected)
            if auto is not None:
                preferred=self._predilution_options(auto)[0]
                i=self._find_stock_choice_index(combo,int(auto['id']),preferred)
                if i>=0:combo.setCurrentIndex(i);chosen=True
        if not chosen:
            if selected:
                combo.setCurrentIndex(-1);combo.setEditText(selected)
            elif combo.count():combo.setCurrentIndex(0)
        combo.blockSignals(False)
        self._sync_combo_display(combo)

    def _new_combo(self,material_id=None,material_name='',original_name='',disabled=False,allow_auto_match=False,selected_predilution_pct=None):
        combo=NoWheelComboBox();combo.setEditable(True);combo.setInsertPolicy(QComboBox.NoInsert)
        self._populate_combo(combo,material_id,material_name,original_name,disabled,allow_auto_match,selected_predilution_pct)
        combo.currentIndexChanged.connect(lambda _idx,c=combo:self._material_combo_changed(c))
        if combo.lineEdit():
            combo.lineEdit().textEdited.connect(lambda text,c=combo:self._material_combo_text_edited(c,text))
            combo.lineEdit().editingFinished.connect(lambda c=combo:self._typed_material_finished(c))
        return combo

    def _typed_material_finished(self,combo):
        if self._loading:return
        data=combo.currentData()
        if data in (self.ORIGINAL_SENTINEL,self.SKIP_SENTINEL):return
        # If the user picked a real dropdown item, currentData is already the source of truth.
        # Do not reinterpret the compact closed-cell label as a newly typed material on focus loss.
        if combo.currentIndex()>=0 and self._stock_choice(data)[0] is not None:
            self._sync_combo_display(combo)
            return
        name=combo.currentText().strip()
        if not name:return
        m=self._best_material_for_typed_name(name)
        if m is not None:
            self._populate_combo(combo,m['id'],m['name'],combo.property('original_name') or name,False,False,self._predilution_options(m)[0])
        else:
            self._populate_combo(combo,None,name,combo.property('original_name') or name,False,False,None)
        self.formula_changed()

    def _row_record(self,r):
        combo=self.table.cellWidget(r,self.COL_MATERIAL)
        part_item=self.table.item(r,self.COL_PARTS)
        try:
            exact=part_item.data(Qt.UserRole) if part_item is not None else None
            parts=float(exact if exact is not None else ((part_item.text() if part_item else '').strip() or 0))
        except Exception:parts=0.0
        if parts<=0 or not isinstance(combo,QComboBox):return None
        override_item=self.table.item(r,self.COL_OVERRIDE)
        manual_override=None
        if override_item is not None:
            try:
                raw=override_item.data(Qt.UserRole)
                if raw is None and override_item.text().strip():raw=float(override_item.text().strip().replace(',','.'))
                if raw is not None:manual_override=min(100.0,max(0.000001,float(raw)))
            except Exception:manual_override=None
        original=str(combo.property('original_name') or '').strip()
        data=combo.currentData()
        if data==self.SKIP_SENTINEL:
            if not original:return None
            return {'row':r,'parts':parts,'material_id':None,'material_name':original,'original_name':original,'disabled':True,'selected_predilution_pct':None,'manual_override_pct':manual_override,'line':None}
        if data==self.ORIGINAL_SENTINEL:
            if not original:return None
            line=FormulaLine(None,original,parts,None,0.0,0.0,False,manual_override)
            return {'row':r,'parts':parts,'material_id':None,'material_name':original,'original_name':original,'disabled':False,'selected_predilution_pct':None,'manual_override_pct':manual_override,'line':line}
        m=None;selected_pred=None
        mid,selected_pred=self._stock_choice(data)
        if mid is not None:m=self.db.material_by_id(mid)
        name=combo.currentText().strip()
        if m is None and name:
            auto=self._best_material_for_typed_name(name)
            if auto is not None:m=auto
        if m is not None:
            conc=float(selected_pred) if selected_pred is not None else self._predilution_options(m)[0]
            if not original:original=m['name']
            line=FormulaLine(int(m['id']),m['name'],parts,conc,float(m['unit_cost_per_g'] or 0),float(m['stock_g'] or 0),True,manual_override)
            return {'row':r,'parts':parts,'material_id':int(m['id']),'material_name':m['name'],'original_name':original,'disabled':False,'selected_predilution_pct':conc,'manual_override_pct':manual_override,'line':line}
        if not name:name=original
        if not name:return None
        if not original:original=name
        line=FormulaLine(None,name,parts,None,0.0,0.0,False,manual_override)
        return {'row':r,'parts':parts,'material_id':None,'material_name':name,'original_name':original,'disabled':False,'selected_predilution_pct':None,'manual_override_pct':manual_override,'line':line}

    def current_records(self):
        out=[]
        for r in range(self.table.rowCount()):
            rec=self._row_record(r)
            if rec is not None:out.append(rec)
        return out

    def _resort_table_by_parts(self):
        """Keep the visible Formulator rows in descending Parts / 1000 order.

        The table contains combo-box cell widgets, so QTableWidget's text sorter is not used here.
        Instead, once every populated row has a positive parts value, rows are rebuilt from their
        current semantic records. This preserves substitutions, selected predilutions, disabled
        originals, and manual overrides while making the visual order deterministic.
        """
        if self._loading:
            return False
        records=self.current_records()
        # Do not make an unfinished '+ Material' row disappear while the user is still entering it.
        if len(records)!=self.table.rowCount() or len(records)<2:
            return False
        indexed=list(enumerate(records))
        ordered=sorted(indexed,key=lambda pair:(-float(pair[1]['parts']),pair[0]))
        if [i for i,_rec in ordered]==list(range(len(records))):
            return False

        current_row=self.table.currentRow();current_col=max(0,self.table.currentColumn())
        current_key=None
        if 0<=current_row<len(records):
            rec=records[current_row]
            current_key=(rec.get('original_name') or '',rec.get('material_name') or '',float(rec.get('parts') or 0),bool(rec.get('disabled')))

        old_loading=self._loading;old_block=self.table.blockSignals(True)
        self._loading=True
        try:
            self.table.setRowCount(0)
            selected_row=0
            for new_row,(_old_idx,rec) in enumerate(ordered):
                self.add_line(rec.get('material_id'),rec.get('parts',0.0),at_row=self.table.rowCount(),
                              material_name=rec.get('material_name',''),original_name=rec.get('original_name',''),
                              disabled=bool(rec.get('disabled')),allow_auto_match=False,
                              selected_predilution_pct=rec.get('selected_predilution_pct'),
                              manual_override_pct=rec.get('manual_override_pct'))
                key=(rec.get('original_name') or '',rec.get('material_name') or '',float(rec.get('parts') or 0),bool(rec.get('disabled')))
                if current_key is not None and key==current_key:selected_row=new_row
        finally:
            self._loading=old_loading
            self.table.blockSignals(old_block)
        if self.table.rowCount():self.table.setCurrentCell(min(selected_row,self.table.rowCount()-1),min(current_col,len(self.HEADERS)-1))
        self.recalc()
        return True

    def current_row_lines(self):
        return [(x['row'],x['line']) for x in self.current_records() if not x['disabled'] and x['line'] is not None]

    def current_lines(self):return [x[1] for x in self.current_row_lines()]

    def refresh_material_combos(self):
        for r in range(self.table.rowCount()):
            rec=self._row_record(r)
            combo=self.table.cellWidget(r,self.COL_MATERIAL)
            if not isinstance(combo,QComboBox):continue
            if rec is None:
                self._populate_combo(combo,None,combo.currentText().strip(),str(combo.property('original_name') or ''),False,False,None)
            else:
                self._populate_combo(combo,rec['material_id'],rec['material_name'],rec['original_name'],rec['disabled'],False,rec.get('selected_predilution_pct'))
        self.recalc()

    def refresh_formulas(self,select_id=None):
        self.formula.blockSignals(True);self.formula.clear();rows=self.db.query('SELECT * FROM formulas ORDER BY name COLLATE NOCASE')
        for f in rows:self.formula.addItem(f['name'],f['id'])
        if select_id:
            i=self.formula.findData(select_id);self.formula.setCurrentIndex(i if i>=0 else 0)
        self.formula.blockSignals(False);self.load_formula()

    def _ask_name(self,title,label):
        from PySide6.QtWidgets import QInputDialog
        return QInputDialog.getText(self,title,label)

    def _backup_existing_title(self,name):
        rows=self.db.query('SELECT id FROM formulas WHERE name=?',(name,))
        if not rows:return None
        fid=int(rows[0]['id']);backup_formula_txt(self.db,fid,self.data_dir);return fid

    def new_formula(self):
        name,ok=self._ask_name('New formula','Formula name')
        if not (ok and name):return
        name=name.strip()
        try:
            existing=self._backup_existing_title(name)
            if existing is not None:
                self.db.execute('DELETE FROM formula_items WHERE formula_id=?',(existing,))
                self.db.execute("UPDATE formulas SET batch_g=100,fragrance_load_pct=20,ifra_category='4',notes='' WHERE id=?",(existing,))
                fid=existing
            else:
                fid=self.db.execute('INSERT INTO formulas(name,batch_g,fragrance_load_pct,ifra_category,notes) VALUES(?,?,?,?,?)',(name,100,20,'4',''))
            write_formula_txt(self.db,fid,self.data_dir)
            self.refresh_formulas(fid);self.formulas_changed_callback and self.formulas_changed_callback()
        except Exception as e:QMessageBox.critical(self,'Create failed',str(e))

    def import_formula(self):
        path,_=QFileDialog.getOpenFileName(self,'Import Formula','',
            'Formula files (*.xml *.pdf *.csv *.tsv *.txt *.xlsx *.xlsm *.png *.jpg *.jpeg *.bmp *.tif *.tiff *.webp);;All files (*.*)')
        if not path:return
        try:
            formulas=import_formula_file(path)
            if not formulas:raise ValueError('No usable formula rows were detected.')
            backed=set()
            for f in formulas:
                title=(f.name or 'Imported Formula').strip() or 'Imported Formula'
                if title not in backed:
                    self._backup_existing_title(title);backed.add(title)
            result=store_imported_formulas(self.db,formulas,create_missing_materials=False,overwrite_existing=True);ids=result.get('formula_ids') or []
            for fid in ids:write_formula_txt(self.db,fid,self.data_dir)
            self.refresh_formulas(ids[-1] if ids else None);self.formulas_changed_callback and self.formulas_changed_callback()
            msg=f"Imported {result['formulas']} formula(s), normalized each to exactly 1000 active parts, and saved TXT recipe file(s) in user_data."
            if result.get('overwritten'):msg+=f"\nOverwrote {len(result['overwritten'])} same-title formula(s); the previous TXT version was copied to formula_backup."
            if result.get('unmatched'):msg+=f"\n{len(result['unmatched'])} material(s) are not in Inventory and remain as red Original choices."
            QMessageBox.information(self,'Formula imported',msg)
        except Exception as e:QMessageBox.critical(self,'Formula import failed',str(e))

    def paste_formula(self):
        dlg=PasteFormulaDialog(self)
        if dlg.exec()!=QDialog.Accepted:return
        try:
            imported=parse_pasted_formula_text(dlg.text.toPlainText(),dlg.name.text())
            self._backup_existing_title(imported.name)
            result=store_imported_formulas(self.db,[imported],create_missing_materials=False,overwrite_existing=True);ids=result.get('formula_ids') or []
            for fid in ids:write_formula_txt(self.db,fid,self.data_dir)
            self.refresh_formulas(ids[-1] if ids else None);self.formulas_changed_callback and self.formulas_changed_callback()
            msg='Pasted formula normalized to 1000 parts and saved as TXT in user_data.'
            if result.get('overwritten'):msg+=' The previous same-title TXT was backed up first.'
            if result.get('unmatched'):msg+=f" {len(result['unmatched'])} material(s) are missing from Inventory and remain red."
            QMessageBox.information(self,'Formula pasted',msg)
        except Exception as e:QMessageBox.critical(self,'Paste Formula failed',str(e))

    def export_formula(self):
        fid=self.formula.currentData()
        if not fid:return
        self.save_formula(show_message=False)
        default=(self.formula.currentText() or 'Formula')+'.txt'
        path,_=QFileDialog.getSaveFileName(self,'Export formula',default,'Formula TXT (*.txt)')
        if not path:return
        try:
            if not str(path).lower().endswith('.txt'):path=str(path)+'.txt'
            Path(path).write_text(formula_text_from_db(self.db,fid),encoding='utf-8')
            QMessageBox.information(self,'Exported','Exported as human-readable TXT using normalized active parts / 1000.')
        except Exception as e:QMessageBox.critical(self,'Export failed',str(e))

    def delete_formula(self):
        fid=self.formula.currentData();title=self.formula.currentText().strip()
        if fid and QMessageBox.question(self,'Delete','Delete this formula?')==QMessageBox.Yes:
            self.db.execute('DELETE FROM formulas WHERE id=?',(fid,));remove_formula_txt(self.data_dir,title);self.refresh_formulas();self.formulas_changed_callback and self.formulas_changed_callback()

    def add_line(self,material_id=None,parts=0.0,at_row=None,material_name='',original_name='',disabled=False,allow_auto_match=True,selected_predilution_pct=None,manual_override_pct=None):
        if isinstance(material_id,bool):material_id=None
        if at_row is None:
            current=self.table.currentRow();r=current+1 if current>=0 else self.table.rowCount()
        else:r=max(0,min(int(at_row),self.table.rowCount()))
        self.table.insertRow(r);self.table.setCellWidget(r,self.COL_MATERIAL,self._new_combo(material_id,material_name,original_name,disabled,allow_auto_match,selected_predilution_pct))
        part_item=QTableWidgetItem(self._fmt_parts(parts));part_item.setData(Qt.UserRole,float(parts or 0));self.table.setItem(r,self.COL_PARTS,part_item)
        for c in range(self.COL_WEIGHT,len(self.HEADERS)):
            it=QTableWidgetItem('')
            if c==self.COL_OVERRIDE:
                if manual_override_pct is not None:
                    try:
                        v=min(100.0,max(0.000001,float(manual_override_pct)));it.setData(Qt.UserRole,v);it.setText(f'{v:g}')
                    except Exception:it.setData(Qt.UserRole,None)
            else:
                it.setFlags(it.flags() & ~Qt.ItemIsEditable)
            self.table.setItem(r,c,it)
        if not self._loading:
            self.table.setCurrentCell(r,self.COL_PARTS);self.formula_changed()
        return r

    def remove_line(self):
        rows=sorted({x.row() for x in self.table.selectedIndexes()},reverse=True)
        for r in rows:self.table.removeRow(r)
        self.formula_changed()

    def clear_formula_cells(self,indexes):
        old=self.table.blockSignals(True)
        try:
            for idx in indexes:
                r,c=idx.row(),idx.column()
                if c==self.COL_MATERIAL:
                    combo=self.table.cellWidget(r,c)
                    if isinstance(combo,QComboBox):
                        original=str(combo.property('original_name') or '').strip()
                        if original and combo.findData(self.SKIP_SENTINEL)>=0:combo.setCurrentIndex(combo.findData(self.SKIP_SENTINEL))
                        else:combo.setCurrentIndex(0)
                elif c==self.COL_PARTS:
                    item=self.table.item(r,c)
                    if item:item.setText('');item.setData(Qt.UserRole,None)
                elif c==self.COL_OVERRIDE:
                    item=self.table.item(r,c)
                    if item:item.setText('');item.setData(Qt.UserRole,None)
        finally:self.table.blockSignals(old)
        self.formula_changed()

    def load_formula(self):
        fid=self.formula.currentData();self._loading=True;self.autosave_timer.stop();self.table.setRowCount(0)
        try:
            if not fid:
                self.formula_note.blockSignals(True);self.formula_note.clear();self.formula_note.blockSignals(False)
            if fid:
                f=self.db.query('SELECT * FROM formulas WHERE id=?',(fid,))[0]
                self.batch.blockSignals(True);self.loadpct.blockSignals(True);self.formula_note.blockSignals(True)
                self.batch.setValue(float(f['batch_g']));self.loadpct.setValue(float(f['fragrance_load_pct']));self.formula_note.setText(f['notes'] or '')
                self.batch.blockSignals(False);self.loadpct.blockSignals(False);self.formula_note.blockSignals(False)
                items=self.db.query("""SELECT fi.*, COALESCE(m.name,NULLIF(TRIM(fi.material_name),''),NULLIF(TRIM(fi.original_material_name),''),'') AS display_name
                                       FROM formula_items fi LEFT JOIN materials m ON m.id=fi.material_id
                                       WHERE fi.formula_id=? ORDER BY fi.parts DESC,fi.sort_order,fi.id""",(fid,))
                for x in items:
                    original=(x['original_material_name'] or x['material_name'] or x['display_name'] or '').strip()
                    selected=(x['display_name'] or x['material_name'] or original).strip()
                    self.add_line(x['material_id'],x['parts'],at_row=self.table.rowCount(),material_name=selected,
                                  original_name=original,disabled=bool(x['disabled']),allow_auto_match=False,selected_predilution_pct=x['selected_predilution_pct'],
                                  manual_override_pct=x['manual_override_pct'] if 'manual_override_pct' in x.keys() else None)
        finally:self._loading=False
        self.recalc()
        if self.active_formula_changed_callback:self.active_formula_changed_callback()

    def formula_changed(self,*args):
        if self._loading:return
        if args and isinstance(args[0],QTableWidgetItem):
            item=args[0]
            if item.column()==self.COL_PARTS:
                try:item.setData(Qt.UserRole,float(item.text().strip().replace(',','.')) if item.text().strip() else None)
                except Exception:item.setData(Qt.UserRole,None)
            elif item.column()==self.COL_OVERRIDE:
                text=item.text().strip().replace(',','.')
                try:
                    v=float(text) if text else None
                    if v is not None:v=min(100.0,max(0.000001,v))
                    item.setData(Qt.UserRole,v)
                except Exception:item.setData(Qt.UserRole,None)
        self.recalc();self.schedule_autosave()

    def _neat_inventory_map(self):
        out={}
        for m in self.db.list_materials():
            opts=self._predilution_options(m)
            if not any(abs(x-100.0)<1e-9 for x in opts):continue
            key=str(m['name'] or '').casefold();candidate=FormulaLine(m['id'],m['name'],0.0,100.0,float(m['unit_cost_per_g'] or 0),float(m['stock_g'] or 0),True)
            if key not in out or candidate.stock_g>out[key].stock_g:out[key]=candidate
        return out

    def _clear_row_style(self,r):
        combo=self.table.cellWidget(r,self.COL_MATERIAL)
        if isinstance(combo,QComboBox):combo.setStyleSheet('');combo.setToolTip('')
        for c in range(1,len(self.HEADERS)):
            item=self.table.item(r,c)
            if item:
                if c>=self.COL_WEIGHT:item.setText('')
                item.setForeground(QBrush())

    def _color_row(self,r,color,tooltip=''):
        combo=self.table.cellWidget(r,self.COL_MATERIAL)
        if isinstance(combo,QComboBox):
            combo.setStyleSheet(f'QComboBox {{ color: {color}; font-weight: 700; }}')
            if tooltip:combo.setToolTip(tooltip)
        for c in range(1,len(self.HEADERS)):
            item=self.table.item(r,c)
            if item:item.setForeground(QBrush(QColor(color)))

    def recalc(self,*args):
        if self._loading or self._recalculating:return
        self._recalculating=True
        try:
            records=self.current_records();enabled=[x for x in records if not x['disabled'] and x['line'] is not None]
            lines=[x['line'] for x in enabled]
            plan=calculate_manufacturing_plan(lines,self.batch.value(),self.loadpct.value(),self._neat_inventory_map(),self._solvent_price_per_g())
            self.table.blockSignals(True)
            low_weight=[]
            try:
                for r in range(self.table.rowCount()):self._clear_row_style(r)
                for rec in records:
                    if rec['disabled']:
                        self._color_row(rec['row'],self.GRAY,'This formula material is intentionally not used in the current making plan.')
                for rec,x in zip(enabled,plan.lines):
                    r=rec['row'];mg=None if x.weight_g is None else x.weight_g*1000.0
                    values={
                        self.COL_WEIGHT:self._fmt_weight_mg(mg),
                        self.COL_INV:'' if x.inventory_concentration_pct is None else f'{x.inventory_concentration_pct:g}',
                        self.COL_OVERRIDE:(f'{rec.get("manual_override_pct"):g}' if rec.get('manual_override_pct') is not None else (f'{x.used_concentration_pct:g}' if x.forced_neat and x.used_concentration_pct is not None else '')),
                        self.COL_COST:'' if x.cost is None else f'{x.cost:.2f}',
                    }
                    for c,text in values.items():
                        item=self.table.item(r,c)
                        if item:item.setText(text)
                    if not x.inventory_available:
                        original=rec['original_name'] or rec['material_name']
                        self._color_row(r,self.RED,f'Original formula material: {original}. Not linked to Inventory. Choose a substitute or Don’t use from the dropdown.')
                    elif x.forced_neat:
                        self._color_row(r,self.RED,f'Too diluted at Inventory {x.inventory_concentration_pct:g}%; use 100% for this batch.')
                    elif mg is not None and 0 < mg <= 5.0:
                        low_weight.append((x.name,mg))
                        self._color_row(r,self.ORANGE,f'{self._fmt_weight_mg(mg)} mg is difficult to dose accurately. Consider a more diluted stock/override.')
            finally:self.table.blockSignals(False)

            if plan.current_strength_without_solvent_pct is None:
                self.current_strength.setText('Current strength without solvent: —')
                self.solvent_summary.setText('Add — g solvent to match strength')
                suffix=' + unresolved material(s)' if plan.missing_inventory_names else ''
                self.cost_summary.setText(f'Cost: {plan.cost_total:.2f}{suffix}')
            else:
                self.current_strength.setText(f'Current strength without solvent: {plan.current_strength_without_solvent_pct:.3f}%')
                self.solvent_summary.setText(f'Add {self._fmt_g(plan.solvent_g)} g solvent to match strength')
                self.cost_summary.setText(f'Cost: {plan.cost_total:.2f}')

            count=len(enabled);parts_text='1000' if count else '0'
            weight_text='—' if plan.weighed_material_total_g is None else f'{self._fmt_g(plan.weighed_material_total_g)} g'
            dilution_text='—' if plan.current_strength_without_solvent_pct is None else f'{plan.current_strength_without_solvent_pct:.3f}%'
            cost_text=f'{plan.cost_total:.2f}' + (' + unresolved' if plan.missing_inventory_names else '')
            self.bottom_summary.setText(f'{count} ingredients  |  {parts_text} parts  |  Weight before adding solvent: {weight_text}  |  Dilution before adding solvent: {dilution_text}  |  Total cost: {cost_text}')

            warnings=[]
            if plan.missing_inventory_names:warnings.append('Missing from Inventory: '+', '.join(plan.missing_inventory_names))
            if plan.forced_neat_count:warnings.append(f'{plan.forced_neat_count} row(s) require a 100% override to reach the requested strength.')
            if low_weight:warnings.append(f'{len(low_weight)} row(s) are ≤5 mg and are highlighted orange for a dilution/override review.')
            self.warning.setText('   |   '.join(warnings));self.warning.setVisible(bool(warnings))
            self.warning.setStyleSheet('color:#d32f2f;font-weight:600;' if (plan.missing_inventory_names or plan.forced_neat_count) else ('color:#ef6c00;font-weight:600;' if low_weight else ''))
        finally:self._recalculating=False

    def save_formula(self,show_message=False):
        fid=self.formula.currentData()
        if not fid:return False
        records=self.current_records()
        # Formula persistence and TXT export always use descending active Parts / 1000 order.
        records=sorted(enumerate(records),key=lambda pair:(-float(pair[1]['parts']),pair[0]))
        records=[rec for _old_index,rec in records]
        normalized=normalize_values_to_1000([x['parts'] for x in records]) if records else []
        try:
            self.db.execute('UPDATE formulas SET batch_g=?,fragrance_load_pct=?,ifra_category=?,notes=? WHERE id=?',(self.batch.value(),self.loadpct.value(),'4',self.formula_note.text().strip(),fid))
            with self.db.connect() as conn:
                conn.execute('DELETE FROM formula_items WHERE formula_id=?',(fid,))
                for order,(rec,parts) in enumerate(zip(records,normalized)):
                    conn.execute("""INSERT INTO formula_items(formula_id,material_id,material_name,original_material_name,disabled,selected_predilution_pct,manual_override_pct,parts,sort_order)
                                    VALUES(?,?,?,?,?,?,?,?,?)""",
                                 (fid,rec['material_id'],rec['material_name'],rec['original_name'] or rec['material_name'],1 if rec['disabled'] else 0,rec.get('selected_predilution_pct'),rec.get('manual_override_pct'),parts,order))
            write_formula_txt(self.db,int(fid),self.data_dir)
            self._resort_table_by_parts()
            if show_message:QMessageBox.information(self,'Saved','Formula saved as normalized 1000 active parts TXT.')
            if self.active_formula_changed_callback:self.active_formula_changed_callback()
            return True
        except Exception as e:
            if show_message:QMessageBox.critical(self,'Save failed',str(e))
            return False

class IFRATab(QWidget):
    'Category 4 analysis for the formula currently active in Formulator.'
    RED='#d32f2f'

    def __init__(self, db, formulator, data_dir: Path, imported_callback=None):
        super().__init__();self.db=db;self.formulator=formulator;self.data_dir=data_dir;self.imported_callback=imported_callback;self._running=False
        lay=QVBoxLayout(self)
        box=QGroupBox('IFRA 51 official data'); bl=QHBoxLayout(box)
        for text,fn in [('Reload bundled IFRA 51',self.reload_bundled),('Import Overview XLSX…',self.import_overview),('Import NCS Annex XLSX…',self.import_annex),('Open IFRA documentation',lambda:webbrowser.open('https://ifrafragrance.org/initiatives-positions/safe-use-fragrance-science/ifra-standards/ifra-standards-documentation'))]:
            b=QPushButton(text);b.clicked.connect(fn);bl.addWidget(b)
        lay.addWidget(box)

        row=QHBoxLayout();self.active_formula=QLabel('Active formula: —');self.active_formula.setStyleSheet('font-weight:600;')
        refresh=QPushButton('Refresh compliance');refresh.clicked.connect(self.run)
        row.addWidget(self.active_formula,1);row.addWidget(QLabel('Category 4'));row.addWidget(refresh);lay.addLayout(row)
        self.data_status=QLabel();lay.addWidget(self.data_status)
        self.status=QLabel();self.status.setWordWrap(True);lay.addWidget(self.status)

        self.table=QTableWidget(0,9)
        self.table.setHorizontalHeaderLabels(['Compliance','Status','IFRA Standard','Actual %','Max %','Excess (mg)','CAS','Type','Sources'])
        hdr=self.table.horizontalHeader()
        for c in range(8):hdr.setSectionResizeMode(c,QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(8,QHeaderView.Stretch)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        lay.addWidget(self.table)
        self.refresh()

    def refresh(self):
        nstd=self.db.query('SELECT COUNT(*) n FROM ifra_standards')[0]['n'];nncs=self.db.query('SELECT COUNT(*) n FROM ncs_contributions')[0]['n'];aliases=self.db.query("SELECT COUNT(*) n FROM material_aliases WHERE source LIKE 'Bundled:%'")[0]['n']
        self.data_status.setText(f'Offline data loaded: {nstd} IFRA 51 standards | {nncs} NCS contribution rows | {aliases} fragrance-name aliases')
        self.run(save_current=False)

    def reload_bundled(self):
        try:
            data = resource_data_dir();overview = data / 'ifra-51st-amendment-ifra-standards-overview.xlsx';annex = data / 'ifra-51st-amendment-annex-on-contributions-from-other-sources.xlsx'
            a=import_ifra_overview(self.db,overview);b=import_ncs_annex(self.db,annex)
            self.refresh();self.imported_callback and self.imported_callback()
            QMessageBox.information(self,'Bundled IFRA 51 reloaded',f"{a['standards']} standards; {b['ncs_contributions']} NCS contribution rows. No network request was used.")
        except Exception as e:QMessageBox.critical(self,'Bundled IFRA reload failed',str(e))

    def import_overview(self):
        p,_=QFileDialog.getOpenFileName(self,'IFRA Standards Overview','','Excel (*.xlsx)')
        if p:
            try:r=import_ifra_overview(self.db,p);self.refresh();self.imported_callback and self.imported_callback();QMessageBox.information(self,'Imported',str(r))
            except Exception as e:QMessageBox.critical(self,'Import failed',str(e))

    def import_annex(self):
        p,_=QFileDialog.getOpenFileName(self,'IFRA Annex','','Excel (*.xlsx)')
        if p:
            try:r=import_ncs_annex(self.db,p);self.refresh();self.imported_callback and self.imported_callback();QMessageBox.information(self,'Imported',str(r))
            except Exception as e:QMessageBox.critical(self,'Import failed',str(e))

    def run(self,*args,save_current=True):
        if self._running:return
        self._running=True
        try:
            fid=self.formulator.formula.currentData();name=self.formulator.formula.currentText().strip()
            if not fid:
                self.active_formula.setText('Active formula: —');self.table.setRowCount(0);self.status.setText('No active formula.');return
            self.active_formula.setText(f'Active formula: {name}')
            if save_current:self.formulator.save_formula(show_message=False)
            frows=self.db.query('SELECT batch_g,fragrance_load_pct FROM formulas WHERE id=?',(fid,))
            if not frows:return
            batch_g=float(frows[0]['batch_g'] or 0);strength=float(frows[0]['fragrance_load_pct'] or 0)
            try:res=check_formula(self.db,fid,'4')
            except Exception as e:QMessageBox.critical(self,'Compliance failed',str(e));return

            self.table.setRowCount(len(res));bad=0
            for r,x in enumerate(res):
                ratio=x.use_of_limit_pct
                if ratio==float('inf'):ratio_text='∞'
                else:ratio_text='—' if ratio is None else f'{ratio:.1f}%'
                excess=x.excess_mg(batch_g);excess_text='' if excess<=1e-12 else FormulatorTab._fmt_weight_mg(excess)
                vals=[ratio_text,x.status,x.name,fnum(x.actual_pct,6),'' if x.max_pct is None else fnum(x.max_pct,6),excess_text,x.cas,x.standard_type,'\n'.join(x.sources)]
                is_over=x.status in ('OVER','PROHIBITED') or ratio==float('inf') or (ratio is not None and ratio>100.0+1e-9)
                if is_over:bad+=1
                for c,v in enumerate(vals):
                    item=QTableWidgetItem(str(v));self.table.setItem(r,c,item)
                    if is_over:item.setForeground(QBrush(QColor(self.RED)))
                if is_over:
                    for c in range(self.table.columnCount()):
                        item=self.table.item(r,c)
                        if item:item.setBackground(QBrush(QColor('#ffebee')))

            unresolved=self.db.query("""SELECT COUNT(*) n FROM formula_items fi LEFT JOIN materials m ON m.id=fi.material_id
                                        WHERE fi.formula_id=? AND COALESCE(fi.disabled,0)=0
                                          AND (fi.material_id IS NULL OR TRIM(COALESCE(m.cas,''))='')""",(fid,))[0]['n']
            detail=f'Active batch {batch_g:g} g at {strength:g}% finished strength. Results are sorted by percentage of the Category 4 limit used.'
            if bad:detail+=f' {bad} substance(s) exceed the limit; Excess (mg) is the excess restricted-substance mass in this finished batch.'
            if unresolved:detail+=f' {unresolved} active formula row(s) cannot be fully checked because Inventory/CAS identity is unresolved.'
            detail+=' This is a calculation aid, not IFRA certification.'
            self.status.setText(detail)
        finally:self._running=False


class SettingsTab(QWidget):
    """Application-wide manufacturing settings. Prices are unitless and use the user's chosen currency consistently."""
    def __init__(self, db, changed_callback=None):
        super().__init__();self.db=db;self.changed_callback=changed_callback
        lay=QVBoxLayout(self)
        box=QGroupBox('Cost settings');form=QFormLayout(box)
        self.solvent_price=QDoubleSpinBox();self.solvent_price.setRange(0,1e12);self.solvent_price.setDecimals(6);self.solvent_price.setSingleStep(0.01)
        rows=self.db.query("SELECT value FROM app_meta WHERE key='solvent_price_per_g'")
        if rows:
            try:self.solvent_price.setValue(float(rows[0]['value']))
            except Exception:pass
        self.solvent_price.valueChanged.connect(self._save)
        form.addRow('Solvent price / g',self.solvent_price)
        note=QLabel('Use the same currency as Inventory prices. Solvent cost uses the finished-strength solvent fraction (for example 20% strength = 80% of batch weight) and is added to Total Cost.')
        note.setWordWrap(True);form.addRow(note)
        lay.addWidget(box);lay.addStretch()
    def _save(self,*_args):
        self.db.execute("INSERT INTO app_meta(key,value) VALUES('solvent_price_per_g',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(f'{self.solvent_price.value():.12g}',))
        if self.changed_callback:self.changed_callback()


class MainWindow(QMainWindow):
    def __init__(self, db, data_dir: Path):
        super().__init__();self.db=db;self.setWindowTitle('Perfume Studio');self.resize(1450,850)
        tabs=QTabWidget();self.setCentralWidget(tabs)
        self.formulator=FormulatorTab(db,data_dir);self.inventory=InventoryTab(db,self.materials_changed);self.ifra=IFRATab(db,self.formulator,data_dir,self.inventory.ifra_data_changed)
        self.settings=SettingsTab(db,self.formulator.recalc)
        self.formulator.materials_changed_callback=self.inventory.refresh
        self.formulator.formulas_changed_callback=self.ifra.refresh
        self.formulator.active_formula_changed_callback=lambda:self.ifra.run(save_current=False)
        self.ifra.run(save_current=False)
        tabs.addTab(self.formulator,'Formulator');tabs.addTab(self.inventory,'Inventory');tabs.addTab(self.ifra,'IFRA');tabs.addTab(self.settings,'Settings')
    def materials_changed(self):
        self.formulator.refresh_material_combos()
