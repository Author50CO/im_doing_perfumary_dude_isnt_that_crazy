# Perfume Studio MVP 0.9.10

## What changed in 0.9.10
- Formulator `Override %` is directly editable. A manual value is saved per formula row and changes the manufacturing weight without changing active Parts / 1000. Blank keeps automatic behavior.
- Automatic 100% overrides no longer require a separate neat-price Inventory row. Cost is calculated from the actual grams weighed using the selected Inventory material's existing price/g.
- Formula row Cost and Total Cost are rounded to two decimal places.
- Added a `Settings` tab immediately after IFRA with `Solvent price / g`. Prices remain currency-symbol-free; use the same user-chosen currency as Inventory.
- Solvent cost is included in the Formulator Cost summary and footer Total Cost.
- Existing databases are migrated with a persistent `manual_override_pct` field.

## What changed in 0.9.9
- Material Details no longer has a Notes tab. `Top / Mid / Base`, `Scent Group`, and the short Inventory Notes field now live directly in the Details tab.
- Scent Groups are compact reusable labels with only a name and user-selected color. A material selects one group from a dropdown; `Add Scent Group` creates a new name/color entry and selects it immediately.
- Inventory's first visible column is now `Group`, rendered compactly as e.g. `Top · Fruity` and tinted with the selected scent-group color. Older v0.9.8 group-position data is used as a compatibility fallback.
- `run.bat` is now a portable release builder instead of an app launcher. Double-clicking it creates `release/PerfumeStudio_Portable.zip`, containing the EXE and required PyInstaller files; Tesseract-OCR is bundled when available on the build machine.
- The generated release ZIP intentionally contains an empty `user_data` directory so a developer's private inventory/formulas are never packaged by accident. Existing local `dist/PerfumeStudio/user_data` is still preserved across rebuilds.


## What changed in 0.9.8
- Inventory layout now begins `Top / Mid / Base | Name | Predilution % | Price | Gram | Price / gram | Notes | CAS No. | Supplier`. The Notes cell is a normal spreadsheet cell and auto-saves.
- Right-click a material -> **Details** -> **Notes** to create/assign reusable note groups. A group stores a group name (for example `Fruity`), a position (`Top`, `Mid`, or `Base`) and a custom color. The Inventory position column summarizes assigned positions and uses the group color as a visual cue.
- Material Details keeps the longer odor/character notes separately from the short Inventory note.
- Density enrichment is conservative: PubChem experimental density is preferred; if none is usable, CompTox experimental density is tried; only then is CompTox predicted density accepted and visibly labelled as predicted.
- A CompTox tab caches DTXSID, density, data type/source text and fetch time. The app can use an optional `CTX_API_KEY` or `COMPTOX_API_KEY` environment variable, but does not require a new setting field.
- Currency and Location remain intentionally absent from all user-facing material forms.


## What changed in 0.9.7
- Removed Currency and Location from material input/details. `Price`, `Price / gram`, and Formula `Cost` are currency-symbol-free numbers; use one currency consistently for your Inventory.
- `Refresh PubChem by CAS` now also reads PubChem PUG-View experimental Density annotations and normalizes supported values to g/mL.
- If Density is empty and PubChem provides a clear room-temperature/experimental value, the Details dialog fills it automatically and displays the raw source annotation. Conflicting values are shown but are not auto-selected.
- Existing databases remain compatible; old `currency` / `location` columns are retained internally but no longer shown or requested.


## What changed in 0.9.6
- Formula export is now human-readable TXT rather than XML. The TXT format is `title - <name>`, then `parts - material`, then `note - <text>`. The compact rounded Parts/1000 shown on screen does not replace the exact internal parts value used for TXT serialization. The compact rounded Parts/1000 shown on screen does not replace the exact internal parts value used for TXT serialization.
- Every formula autosave mirrors the normalized active-parts recipe directly into `user_data/<title>.txt`. Formula rows in TXT sum to exactly 1000 parts.
- Import remains multi-format, including legacy XML, PDF/OCR, CSV/TSV/TXT, Excel and images. The new TXT format can also be imported directly.
- Import/Paste/New with a title that already exists overwrites the formula instead of creating `(<n>)`. Before overwrite, the previous recipe is copied to `user_data/formula_backup/<title>-YYYY-mon-DD backup.txt`; repeated same-day backups get a numeric suffix rather than replacing a backup.
- Added an editable Formula Note field; it is saved to the TXT `note -` line.
- Inventory rows now have a right-click `Details` action. The dialog edits extended material metadata and shows local IFRA 51 data for the material CAS.
- Material Details can fetch and cache PubChem PUG REST identity data by CAS: CID, title, IUPAC name, molecular formula/weight, SMILES, InChIKey, description and synonyms.
- Added optional `Top / Middle / Base` note-position and odor/character-note fields in Material Details for future olfactory classification work.

## What changed in 0.9.5
- `Parts / 1000` display is now compact and manufacturing-friendly: values >=10 show as whole numbers; values below 10 show at most two decimal places; trace values below 0.01 fall back to two significant figures.
- This is display-only. Formula storage and TXT export retain the normalized active-parts values and are not rounded to the on-screen display.
- Keeps the 0.9.4 fixes for persistent substitute selections and clean Material-cell labels.


## What changed in 0.9.3
- `Parts / 1000` values below 1 part show at least two significant figures while preserving trace-material precision.
- Material choices are stock-specific: one Inventory row with `Predilution % = 10, 1` appears as separate `10% Material` and `1% Material` choices in Formulator, and the selected stock concentration persists with the formula row.
- Formula XML export remains dilution-independent: only active recipe parts are exported, normalized to exactly 1000 displayed parts.
- IFRA Category 4 follows the active Formulator formula automatically. Results are sorted by compliance usage (`actual / max × 100`) descending; over-limit rows are red and show excess restricted-substance mg for the current batch.


## What changed in 0.9.2
- Batch size displays whole grams without forced decimal zeroes while still accepting decimal input.
- Formulator weight is shown in mg with no decimals from 1 mg upward; weights at or below 5 mg are highlighted orange for practical dosing review.
- The Formulator footer shows ingredient count, `1000 parts`, weight before solvent, dilution before solvent, and total cost.
- Inventory accepts multiple predilutions in one cell (`10, 20`). The first value is used as the default stock concentration for the current making calculation.
- Pasting the same material repeatedly no longer creates a new row once the CAS matches an existing Inventory row. Predilution values are merged into the existing row.
- Missing imported formula materials keep their original identity. Their Material dropdown starts with the red original formula material, then `Don’t use this material`, then Inventory substitutes. The original name is preserved even after a substitute is selected.
- Formula items store original identity, selected Inventory link, disabled state, and parts separately. Inventory still does not receive missing formula materials automatically.

## What changed in 0.9.1
- Formula rows now preserve their own material name independently from Inventory. Missing Inventory matches are kept in the formula, displayed in red, and are never auto-created as Inventory rows.
- Inventory deletion no longer fails when a formula references that material. Formula links use `ON DELETE SET NULL`; the formula row remains and becomes unresolved/red. Existing databases are migrated automatically.
- Formulator columns are now exactly: `Material | Parts / 1000 | Weight (mg) | Inventory % | Override % | Cost`. Formula %, Active mg, stock-after and footer totals were removed.
- Batch controls now show `Current strength without solvent`, `Add X g solvent to match strength`, and `Cost` beside Batch size / Finished strength.
- Added `Paste Formula`, with a large text field for pasted two-column formulas. Values are normalized to 1000 active parts and unmatched materials remain formula-only/red.
- Manufacturing calculations refuse to invent weights for missing Inventory materials; current strength and solvent addition stay unresolved until every formula material has an Inventory match.
- Existing too-diluted behavior is retained: largest diluted materials are overridden to 100% first, and affected rows are red.
- Formula-to-Inventory matching can also reuse learned alias/CAS mappings, not only exact visible names.

## Formulator semantics

The formula itself is always **ACTIVE material composition** and is stored/exported as **parts per thousand (1000 total)**. Inventory predilution is never baked into the formula. It is used only to calculate what stock solution you physically weigh.

Example: if Cashmeran is 35 parts/1000 active and Inventory says Cashmeran is 10%, the Material cell still displays `Cashmeran`; the weighing plan uses the 10% stock concentration and shows the required **Weight mg**. If you later change that Inventory row to 20%, the formula remains 35 parts/1000 and the required stock weight automatically halves.

Set **Batch** and **Finished perfume strength** to get a complete making plan. If diluted stocks make the requested strength impossible, the calculator switches the largest diluted formula materials to 100% in descending parts order until it fits. Those rows turn red. The top summary shows current stock-mixture strength, additional perfume solvent required, and cost.

Legacy XML from `Author50CO/im_doing_perfumary_dude_isnt_that_crazy` is supported. In those files, `row.part` is already the pure-material-equivalent value used by the original calculator, while `manual_dilution` describes the stock concentration.

---

Python/PySide6 desktop perfumery tool focused on **Formulator + Inventory + GCMS import + IFRA Category 4**.



## What changed in 0.8.2
- Resolve Enrich Error now returns up to 50 relevant identity candidates instead of truncating to 4.
- Candidate count is independent from relevance filtering: unrelated autocomplete fillers are discarded rather than used to fill the list.
- Added a conservative lexical-relevance gate to reject suffix-only coincidences such as Muscenone -> Ionone Gamma / Damascenone.
- Exact PubChem synonym resolution is attempted separately so a real trade-name -> chemical-name bridge does not need fuzzy spelling similarity.
- Resolver labels show match score and combo boxes can scroll through long result lists.

## What changed in 0.8.1

- Fixed the `Muscenone` resolver without adding a query-specific `if name == ...` rule. The bundled identity corpus now contains the independently sourced identities `(R,Z)-5-Muscenone` (CAS 464207-51-0) and `MUSCENONE DELTA` (CAS 63314-79-4). Generic fuzzy ranking therefore surfaces both when the inventory query is `Muscenone`.
- The resolver no longer falls back to noisy PubChem autocomplete when two strong local candidates already explain the query.
- Added a final **Search by CAS number** section to `Resolve Enrich Error`. It searches exact CAS tokens across learned aliases, bundled identities, IFRA 51 Standards, IFRA NCS, cached Transparency rows, and existing Inventory. If no local identity exists, PubChem is queried automatically for the CAS.
- A CAS-search result can be attached to any unresolved inventory row, after which the normal Apply action saves the choice as a learned local alias.

## What changed in 0.8.0

- **Resolver is algorithmic instead of query-hardcoded.** `Resolve Enrich Error` normalizes the inventory name, removes generic product words, ranks all local identities with token/edit similarity, and uses PubChem autocomplete/synonyms automatically only when local data is insufficient. PubChem results are cached.
- PubChem is used primarily for **name discovery**. When a discovered name maps strongly to local IFRA/reference identity data, that local CAS is preferred. PubChem CAS synonyms are only a user-confirmed fallback.
- There is no material-specific `Muscenone -> [fixed options]` / `Cedarwood -> [fixed options]` resolver table. New names go through the same candidate algorithm. User-confirmed choices are still learned as aliases.
- **Inventory auto-saves** after edits/paste/row insertion/resolution; no Save Changes button is required.
- **Formulator and GCMS import are unified.** There is no separate GCMS tab. `Formulator > Import Formula` accepts XML, PDF, CSV/TSV/TXT, XLSX/XLSM and common image formats and creates formula records directly.
- Formula storage has one invariant: every non-empty formula is stored as **parts per thousand (sum = 1000)**. Imports and XML exports are normalized to 1000. Inventory concentration/predilution remains a property of the referenced Inventory row and is not baked into formula parts.
- Existing databases are normalized to the same 1000-parts invariant at startup.
- Formulator spreadsheet supports Delete/Backspace for selected editable cells. Inventory uses the same Excel-like Delete behavior.
- Tesseract discovery was expanded to portable/system locations. On Windows OCR now tries automatic `winget` installation, using both current Tesseract package identifiers as fallbacks. `run.bat` performs a best-effort OCR setup on the first v0.8 run.
- Ordinary supplier recipe PDFs are parsed as `Material | grams/parts` before OCR. Scanned/image formulas fall back to the word-position Tesseract pipeline adapted from the older PerfumeCalculator project.

## What changed in 0.7.0

- **Resolve Enrich Error is now an actual ambiguity resolver.** Remaining unresolved Inventory rows are shown in a dialog with an offline dropdown of up to four plausible identities.
- A chosen identity fills the CAS cell and is saved as a user alias, so that exact inventory name resolves automatically in the future.
- **Muscenone** now offers user-confirmed choices including `(R,Z)-5-Muscenone` (CAS 464207-51-0) and `MUSCENONE® DELTA` (CAS 63314-79-4).
- Generic naturals can expose botanical/source choices instead of inventing one CAS. Bundled examples include Cedarwood (Virginia/Texas/Atlas) and Fir Needle (Silver/Siberian/Balsam).
- Candidate resolution now uses local data first and an automatic cached PubChem fallback when local identity data is insufficient; the user never has to search PubChem manually.

## What changed in 0.6.0

This release integrates two pieces from the older `Author50CO/im_doing_perfumary_dude_isnt_that_crazy` PerfumeCalculator workflow:

- XML formula import compatible with `perfume_tool/formula_storage.py` (`<formula>` and `<formulas>` bundles).
- Tesseract word-position OCR logic adapted from `perfume_tool/ocr.py` for screenshot/scanned GCMS tables, including the old `Formula entry | Weight (g) | % Rel | % Abs` layout.

It also extends the offline identity reference with reviewed Camphor/Hinoki aliases and gives explicit reasons for intentionally unresolved generic natural/trade names such as Cedarwood, Fir Needle and Muscenone.

## Inventory spreadsheet

Visible columns are:

`Name | Predilution % | Price | Gram | Price / gram | CAS No. | Supplier`

- Paste 100+ materials at once with Ctrl+V.
- Paste one name per line or a rectangular Excel / Google Sheets range.
- Click a cell once and type immediately; no double-click is required.
- Column widths are freely draggable.
- `+ Row` inserts immediately below the selected row.
- `Price / gram` is derived automatically from Price and Gram.
- Predilution is parsed from names such as `Evernyl 10%` or `Phenyl acetaldehyde 20%`.
- Same-name rows at different predilutions are supported.
- Editing/committing a Name automatically attempts local CAS enrichment.
- Adding a predilution can inherit CAS from an already-known row of the same material.
- `Resolve Enrich Error` is a batch candidate resolver for selected unresolved rows, or all unresolved rows if nothing is selected. It ranks local candidates and can query/cache PubChem automatically; it never opens a manual web search.
- If you manually enter a CAS for a material that the bundled reference cannot identify safely, that Name → CAS mapping is learned locally and reused next time.

## How CAS identity resolution works

The runtime resolver uses this order:

1. user-confirmed/local learned aliases and existing Inventory identities;
2. all bundled reference + official IFRA 51/NCS names, ranked by generic normalization/token/edit similarity;
3. if local matches are weak, automatic PubChem autocomplete for canonical/synonym name discovery;
4. discovered PubChem names are matched back to local IFRA/reference identities when possible;
5. a PubChem CAS-looking synonym is shown only as a user-confirmed fallback when no strong local identity can be linked.

No query-specific answer table is consulted by the resolver.

The bundled identity snapshot is intentionally compact rather than a complete copy of PubChem. PubChem's full bulk data is very large and PubChem is not an authoritative CAS registry. The snapshot is used to bridge common perfumery trade names (for example Hedione) to a principal chemical identity/CAS without making web requests at runtime.

Ambiguous bases, commercial mixtures and natural materials whose exact species/process changes identity are intentionally left blank rather than guessed.

## IFRA Category 4

The app is fixed to **IFRA Category 4** for this workflow, so category selectors are removed.

- Official IFRA 51 Standards Overview and NCS Annex are bundled and auto-imported when the database is empty.
- No IFRA download is required for normal use.
- The IFRA tab has `Reload bundled IFRA 51` for rebuilding those tables from the files shipped with the application.
- Manual Overview/NCS XLSX import buttons remain available in case you intentionally want to replace the reference files later.
- Natural materials are expanded through NCS contributions when a sufficiently specific matching identity/CAS exists.
- Direct and indirect contributions to the same restricted constituent are aggregated.
- Dilution concentration is accounted for before finished-product exposure is checked.

**Compliance note:** this is a calculation aid, not an IFRA Certificate of Conformity. Individual Standards and current official IFRA guidance remain authoritative for edge cases.

## Formulator

- `Import Formula` imports old PerfumeCalculator XML plus PDF, CSV/TSV/TXT, XLSX/XLSM and images. Missing materials are created in Inventory automatically, preserving stock predilution as an Inventory property.

- Parts and normalized %
- Batch scaling in grams
- Active amount for prediluted materials
- Finished-product fragrance load
- Live formula cost from Inventory price/gram
- Stock-after preview
- IFRA Category 4 compliance

## GCMS Import

- CSV / TSV / TXT
- XLSX
- text/table-based PDF
- scanned PDF OCR fallback
- PNG / JPG / BMP / TIFF / WEBP screenshots
- `Paste GCMS image` from the Windows clipboard
- preserves `% Rel` and `% Abs` separately and uses `% Abs` for formula parts when present
- CAS-first matching, then normalized/fuzzy name matching
- manual inventory mapping before creating a formula

OCR is adapted from the older PerfumeCalculator's Tesseract word-position workflow. It looks for a bundled `Tesseract`/`tesseract` folder first, then a system Tesseract install. If you still have the old calculator's Tesseract folder, place it next to `app.py` before running/building.

## Run on Windows

Double-click `run.bat`.

The first v0.6 launch may briefly show a setup window while Python packages are installed. Normal launches use `pythonw.exe`, so no batch/console window remains behind the app.

User data is stored in:

`user_data/perfume_studio.db`

## Build portable EXE folder

```powershell
powershell -ExecutionPolicy Bypass -File .\build_portable.ps1
```

Output:

`dist/PerfumeStudio/PerfumeStudio.exe`

The builder includes the `data` directory inside the PyInstaller bundle and preserves an existing portable `user_data` directory across rebuilds.

## 0.9.11 stability and solvent costing

Solvent cost is based on the finished perfume strength. A 10 g batch at 20% strength has an 8 g solvent cost basis. The Formulator's `Add X g solvent` remains the practical extra solvent required after stock predilutions are accounted for.

If the application encounters an exception, inspect `user_data/PerfumeStudio.log`. Inventory autosave is intentionally deferred while a cell editor is active, and material-combo refreshes are deferred until after the save event returns.


## 0.9.12 raw-material costing and formula sorting

Inventory prices are assumed to be prices for the purchased 100% raw material. Working-stock predilution changes how many grams are weighed but does not multiply material cost. Ingredient cost is based on active raw-material grams. Formulator recipes are always saved and displayed in descending Parts / 1000 order.
