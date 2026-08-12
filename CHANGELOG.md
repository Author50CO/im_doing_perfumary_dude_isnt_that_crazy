# Perfume Studio MVP 0.9.12

- Fixed raw-material costing: Inventory `price/g` is treated as the purchase price of the 100% material. Predilution affects weighing weight only; ingredient cost is `active grams × raw price/g`.
- Example: 100 g Coumarin purchased for 10,000 gives 100/g. Weighing 10 g of a 10% Coumarin working stock consumes 1 g active Coumarin, so ingredient cost is 100, not 1,000.
- Solvent cost remains separate and is based on the finished perfume solvent fraction from Settings.
- Formulator rows now persist and display in descending `Parts / 1000` order. Imported formulas load sorted, and manual parts edits are re-sorted after autosave without changing the exact stored ratio.
- TXT formula export follows the same descending Parts / 1000 order.

# Perfume Studio MVP 0.9.11

- Solvent COST now uses the requested finished-strength solvent fraction: e.g. 10 g at 20% strength costs 8 g of solvent, regardless of stock predilutions. Practical `Add X g solvent` remains based on solvent already present in prediluted stocks.
- The current row receives a subtle tint whenever a cell is active, improving horizontal readability without selecting the whole row.
- Inventory autosave no longer performs structural duplicate-row merges while an editor is active; duplicate CAS merging remains in the paste workflow.
- Autosave defers while a cell editor is open, reducing the chance of Qt invalidating the editor during a database/UI refresh.
- Inventory -> Formulator material refresh is now deferred/debounced rather than running synchronously inside `itemChanged`/autosave.
- SQLite uses WAL, busy timeout, explicit rollback on failures.
- `user_data/PerfumeStudio.log` plus Python `faulthandler` records unhandled exceptions/native Python faults for future crash diagnosis.
- Inventory cell-update/autosave failures are contained and logged instead of escaping through the Qt event loop.

# v0.9.10

- Override % is now manually editable and persisted per formula row. Manual override concentration changes only the weighing plan, never normalized active formula parts.
- Automatic/manual overrides are costed using actual weighed grams times the selected Inventory row's price/g; no separate 100% price is required.
- Formulator line and total costs are rounded to 2 decimals.
- Added Settings tab with a unitless `Solvent price / g` setting stored in app_meta. Solvent cost is included in final Formula cost.

# v0.9.9

- Moved Top/Mid/Base selection into Material Details -> Details.
- Removed the separate Notes tab; the short Notes value remains editable in Details and in the Inventory spreadsheet.
- Reworked reusable Note Groups into Scent Groups (name + color). Material Details uses a single Scent Group dropdown plus `Add Scent Group`.
- Renamed the Inventory classification column to `Group` and render it compactly as `Position · Scent Group` with the chosen group color.
- Changed `run.bat` into the release-build entry point. It runs PyInstaller and produces `release/PerfumeStudio_Portable.zip`; the ZIP contains an empty user_data folder and does not leak existing local user data.

# v0.9.8

- Inventory now shows a `Top / Mid / Base` column immediately left of Name. The value is derived from the material's assigned reusable note groups and uses the first assigned group color as a quick visual cue.
- Added a directly editable `Notes` column immediately after `Price / gram` for short material descriptions; it auto-saves with the rest of the spreadsheet.
- Material Details now has a dedicated Notes tab with short notes, detailed odor/character notes, and reusable Note Groups. Note groups have a name, Top/Mid/Base position and user-selected color, and can be assigned to any number of materials.
- Density enrichment now follows `PubChem experimental -> EPA CompTox experimental -> EPA CompTox predicted`. CompTox values are explicitly tagged experimental/predicted and cached in `material_details`.
- Added a CompTox tab in Material Details. The CTX API is used when accessible; the public Chemicals Dashboard properties page is a read-only fallback. Optional `CTX_API_KEY` / `COMPTOX_API_KEY` environment variables are supported without adding any currency/location settings.
- Currency and Location remain absent from the user interface.

# v0.9.7

- Removed Currency and Location from all material entry/detail UI. Prices are now intentionally unitless; the user keeps the Inventory in one chosen currency. Legacy DB columns remain only for backwards compatibility.
- Extended PubChem enrichment to query PUG-View `Density` annotations by CID. Supported density units are normalized to g/mL.
- Material Details now shows the source density annotation and automatically fills an empty Density field only when the PubChem annotations yield a clear, non-conflicting preferred value.
- If PubChem contains materially conflicting density values, Perfume Studio leaves Density unchanged and shows the candidate values instead of guessing.

# v0.9.6

- Preserved exact internal Parts/1000 values separately from the compact on-screen rendering, so autosave/export no longer re-parses rounded display text.
- Replaced user-facing XML export with a readable TXT recipe format: `title -`, normalized `parts - material` rows, and `note -`.
- Formula autosave writes `user_data/<title>.txt`; serialized recipe values are decimal-normalized to total exactly 1000 parts.
- Kept legacy XML as an import format. TXT formula files are now importable in the same multi-format importer.
- Same-title New/Import/Paste overwrites the existing formula. The pre-overwrite recipe is backed up to `user_data/formula_backup/<title>-YYYY-mon-DD backup.txt` (with numbered same-day backups when necessary).
- Added Formula Note editing and TXT note persistence.
- Added Inventory right-click `Details` with full editable material metadata.
- Added material reference detail storage. The Details window reads matching Category 4 information from the bundled IFRA 51 database and can fetch/cache PubChem PUG REST identity properties by CAS.
- Added optional Top/Middle/Base and odor-character fields for later note-position analysis.

# v0.9.5

- Changed Formulator `Parts / 1000` display formatting only: values >=10 display as integers; values below 10 use at most two decimal places; trace values below 0.01 use two significant figures. Stored values and XML export precision are unchanged.

# Changelog

## 0.9.4
- Fixed Formulator Inventory substitution choices resetting when the material combo loses focus or the table is scrolled. A selected dropdown item is now authoritative and is committed immediately instead of being reinterpreted as free-typed text by `editingFinished`.
- Formulator Material cells once again display only the clean material name. Predilution is shown only while the Material dropdown is open, where each available stock remains a separate choice such as `10% Vanillin` and `1% Vanillin`.
- The selected predilution continues to be stored independently in `selected_predilution_pct` and displayed in the dedicated `Inventory %` column. Formula parts and exported XML remain dilution-independent.
- Mouse-wheel changes remain disabled for material selectors.

## 0.9.3
- Parts / 1000 display keeps compact precision; values below 1 part always show at least two significant figures (for example `0.50`, `0.070`, `0.00050`).
- XML export continues to contain only active parts-per-thousand recipe composition. Exported enabled rows are re-normalized and serialized so their displayed decimal values total exactly 1000; Inventory predilution choices are not baked into the XML.
- Formulator material dropdown exposes each Inventory predilution as a separate stock choice, e.g. `10% Vanillin` and `1% Vanillin`, even when both concentrations live in one Inventory row. The chosen stock concentration is saved per formula row.
- IFRA no longer has a separate formula selector. It automatically analyzes the formula currently active in Formulator using Category 4.
- IFRA results are sorted by percentage of limit used, highest first. Exceeded/prohibited rows are red and show excess restricted-substance mass in mg for the current finished batch.
- Disabled formula rows are excluded from IFRA exposure calculations.
## 0.9.2
- Batch-size input keeps decimal precision available but hides trailing `.0000` for whole-gram values.
- Weight display is integer milligrams at 1 mg and above; sub-milligram weights retain decimals. Rows at 5 mg or below are highlighted orange as a practical dosing warning.
- Added a compact Formulator footer: ingredient count, 1000 parts, weight before perfume solvent, dilution before solvent, and total cost.
- Inventory `Predilution %` accepts comma-separated stock concentrations such as `10, 20`; the first value is the default manufacturing concentration and the remaining values are retained as available stocks.
- New/pasted Inventory rows whose CAS duplicates an existing Inventory CAS are merged instead of creating another material; predilution options are unioned.
- Formula items now preserve `original_material_name` separately from the selected Inventory substitute and can be marked disabled. Missing imported materials show a red Original option, a Don’t-use option immediately below it, and Inventory replacements in the same dropdown.
- Formula autosave/export preserves 1000-part composition while disabled rows stay stored internally; exported making formulas omit disabled rows and renormalize enabled material ratios to 1000.

## 0.9.1
- Decoupled formula identities from Inventory rows: `formula_items` now preserve `material_name`, allow a NULL Inventory link, and use `ON DELETE SET NULL`. This fixes Inventory row deletion foreign-key failures without deleting formula composition.
- Imports no longer create missing Inventory materials. Unmatched formula materials are retained and shown red until the user adds/matches them in Inventory.
- Simplified Formulator to six columns: Material, Parts / 1000, Weight (mg), Inventory %, Override %, Cost.
- Removed Formula %, Active mg, stock-after and bottom total/ratio footer.
- Added top-line manufacturing summaries: current strength without solvent, grams of solvent to add, and cost.
- Added Paste Formula dialog for direct text/Excel-style formula input; pasted formulas are stored as 1000 active parts.
- Missing Inventory rows make the manufacturing plan explicitly incomplete instead of assuming 100% stock.
- Improved formula-to-Inventory matching by using learned identity/CAS aliases in addition to normalized names.

## 0.9.0
- Formulas are treated as ACTIVE pure-material-equivalent parts and stored/exported only as parts per thousand (sum 1000).
- Legacy Author50CO calculator XML is imported correctly: `row.part` is already the pure-material-equivalent amount; `manual_dilution` is only a stock concentration hint.
- PDF/image formula rows that explicitly name a predilution are converted from stock amount to active parts on import.
- Formulator material names no longer append `[10%]`, `[20%]`, etc. Current Inventory concentration is shown in its own column.
- Batch + Finished perfume strength now generate a direct weighing sheet in milligrams.
- If Inventory predilutions are too dilute for the requested strength, diluted materials are forced to 100% one whole material at a time in descending active-parts order until the target is feasible. Affected rows are red.
- If a real 100% Inventory row exists for a forced material, its stock/cost metadata is used; otherwise the UI warns that neat stock is missing.
- Additional perfume solvent mass is shown in mg so the displayed plan totals exactly to the requested batch.
- Formula material comboboxes ignore mouse-wheel selection changes.
- Imported materials reuse the user's existing same-name Inventory row even when its current predilution differs from the source formula, while preserving the formula's active composition.

# 0.8.2
- Resolver candidate cap increased from 4 to 50.
- Added lexical relevance gating so PubChem autocomplete cannot fill empty slots with suffix-only lookalikes.
- Exact PubChem synonym lookup is a separate candidate path.
- Candidate labels now display match percentage.

# 0.8.1

- Fixed `Muscenone` candidate resolution by expanding the general identity corpus (not the query logic) with verified `(R,Z)-5-Muscenone` / CAS 464207-51-0 and `MUSCENONE DELTA` / CAS 63314-79-4 identities.
- Changed candidate fallback so two strong local identity matches suppress noisy online autocomplete.
- Added exact `Search by CAS number` fallback in the resolver. It checks local aliases/reference identities, IFRA Standards/NCS, cached Transparency data and existing Inventory first, then uses PubChem only if the CAS is absent locally.
- CAS-search results can be assigned to a chosen unresolved inventory row and are learned as a local alias on Apply.
- Added regression tests for the Muscenone dropdown and exact CAS lookup.

# 0.8.0

- Replaced material-specific resolver candidate groups with one generic identity-resolution algorithm: query normalization, generic-word stripping, fuzzy/token ranking over all local aliases/IFRA/NCS identities, and automatic cached PubChem fallback.
- PubChem is used first for name discovery; strong matches are linked back to local IFRA/reference identities for CAS before falling back to a user-confirmed PubChem synonym CAS.
- Added general Heliotropin/Piperonal identity records to the reference snapshot; `Heliotrope Base` can surface this through fuzzy ranking without a query-specific rule.
- Inventory edits now auto-save; the Save Changes workflow is removed.
- Merged GCMS/file import into Formulator under one `Import Formula` action. XML, PDF, CSV/TSV/TXT, XLSX/XLSM and image formulas create formula records directly.
- Enforced formula storage/export invariant: every non-empty formula is normalized to exactly 1000 parts. Existing formulas are migrated on startup.
- Formula rows reference Inventory material IDs; dilution/concentration is not baked into formula parts, so later Inventory dilution edits affect active-material calculations.
- Importing an explicit predilution now requires/selects the matching Inventory concentration row instead of silently attaching to a same-name neat row.
- Added Delete/Backspace clearing in the Formulator spreadsheet and retained Excel-like Delete in Inventory.
- Improved Tesseract discovery and automatic Windows setup: portable/system search plus winget fallback across current Tesseract package identifiers; run.bat attempts setup on first v0.8 run.
- Added ordinary two-column supplier-PDF parsing before OCR; scanned/image formulas use the adapted Tesseract word-position OCR fallback.
- Verified the uploaded ANI 898307 PDF imports as one 20-row formula totaling 1000 parts, including 2% Ambergris Tincture and 10% Damascone Alpha inventory predilutions.

# 0.7.0

- Reworked `Resolve Enrich Error` into a dropdown-based ambiguity resolver.
- Added bundled offline resolution-candidate records for ambiguous trade/generic names.
- Added Muscenone vs MUSCENONE DELTA choices and botanical-source choices for Cedarwood/Fir Needle.
- User-confirmed choices are persisted to the local alias database for automatic future enrichment.
- No runtime web search is used by the resolver.

# 0.6.0

Legacy PerfumeCalculator integration + identity diagnostics:
- Added Formulator `Import XML` for the XML schema used by `perfume_tool/formula_storage.py`, including multi-formula bundles
- XML import preserves row parts and predilution; missing materials are created in Inventory and enriched from the offline identity resolver
- Upgraded GCMS table parsing to preserve `% Rel` and `% Abs` separately and prefer `% Abs` for formula creation
- Added image GCMS import and `Paste GCMS image` using Tesseract word-position OCR adapted from `perfume_tool/ocr.py`
- Added scanned-PDF OCR fallback using PyMuPDF page rendering
- Added Camphor/`Champor` and Hinoki offline aliases; Hinoki resolves to the IFRA-linked Chamaecyparis obtusa CAS
- `Resolve Enrich Error` now explains intentionally ambiguous generic names such as Cedarwood, Fir Needle and Muscenone instead of only showing a blank failure
- Portable builder automatically bundles an existing `Tesseract`/`tesseract` folder when supplied

# 0.5.0

Offline automatic identity/CAS enrichment:
- Removed runtime IFRA Transparency scraping and PubChem web searches; the v0.4 403 failure path no longer exists
- Bundled the official IFRA 51 Standards Overview XLSX and official NCS contributions Annex XLSX for automatic local import
- Added `data/fragrance_identity_reference.sqlite`, a compact perfumery identity snapshot linking reviewed trade/common names to principal names and CAS values
- Startup now installs bundled aliases and automatically enriches every existing Inventory row whose CAS is blank
- `Resolve Enrich Error` is now a batch offline retry, not a manual database search dialog
- User-entered Name → CAS mappings are learned locally and take precedence over future bundled reference updates
- Added conservative aliases for common perfumery trade names, spelling variants and essential-oil names; ambiguous bases/mixtures/species are deliberately left unresolved
- The IFRA tab now uses `Reload bundled IFRA 51`; no network download is required
- Removed `requests` and `beautifulsoup4` runtime dependencies
- PyInstaller build now includes the bundled `data` directory

# 0.4.0

Manual enrichment resolver / trade-name learning:
- Added `Resolve Enrich Error` for selected unresolved Inventory rows (or all unresolved rows when nothing is selected)
- Added local cache table for the official IFRA Transparency List and an in-app refresh/downloader that follows the site's rendered Next pagination
- Resolver searches the cached IFRA Transparency List by user-editable chemical/principal name and lets the user explicitly choose a CAS match
- Added `Open IFRA page` fallback with the current search term copied to the clipboard
- Added PubChem synonym/trade-name lookup as a manual-review fallback; PubChem results are never auto-applied
- Added persistent `material_aliases` table: every manually confirmed trade-name → CAS mapping is reused by automatic enrichment in future
- Confirming one alias also fills duplicate/predilution rows with the same learned name

# 0.3.0

Inventory / Category 4 UX pass:
- Inventory cells now use `AnyKeyPressed` editing: click a cell and type immediately, Excel-style
- Visible Inventory columns use interactive header sizing so every column can be dragged wider/narrower
- `+ Row` inserts directly below the currently selected row instead of always appending at the bottom
- Removed the manual `Enrich CAS from IFRA` button; committing a Name automatically attempts CAS enrichment
- Automatic CAS values are refreshed when an auto-enriched Name changes, while manually entered CAS values are preserved
- Existing Inventory CAS values are also used as a conservative same-material fallback, useful when adding a predilution below a neat material
- IFRA lookup now understands common perfumery list formatting such as `Synthetic`, `(Undiluted)`, parenthetical trade-name aliases, slash aliases, and terminal `100`
- Importing/re-importing IFRA data automatically attempts enrichment of existing blank CAS cells
- IFRA category selectors were removed from Formulator and IFRA tabs; compliance is fixed to Category 4
- Existing formula records are normalized to Category 4 on database startup
- `run.bat` launches the app with `pythonw.exe` after environment setup so the command window does not remain open during normal use

# 0.2.0

Inventory workflow overhaul:
- Replaced modal Add/Edit-first inventory workflow with an Excel-like editable grid
- Visible columns: Name, Predilution %, Price, Gram, Price / gram, CAS No., Supplier
- Multi-row Ctrl+V paste for plain material lists and Excel/Google Sheets tables
- Regex predilution parsing for names such as `Evernyl 10%` and `Ambroxan (20% DPG)`
- Automatic Price / gram derivation from Price and Gram
- Conservative CAS enrichment from already-imported official IFRA Standards and NCS Annex data
- Existing CAS values are never overwritten by enrichment
- Supports duplicate material names at different predilutions while preserving numeric material IDs
- Formulator/GCMS selectors show predilution in material labels

# 0.1.0

Initial MVP:
- Formulator with normalized parts, batch scaling, active concentration and cost
- Inventory with dilution parent linkage
- GCMS CSV/TSV/XLSX/PDF parsing and inventory matching
- IFRA 51 official overview importer
- IFRA 51 official NCS contributions importer
- Finished-product category compliance calculation
