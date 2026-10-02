# Inventory Management Tool (MVP)

Checks, based on a Windchill BOM, which materials are available for a given
number of units (e.g. prototypes), which are missing, and where available
material is located. Successor to the old `prototyping_automation.py`
script, with a corrected core bug: demand is now **aggregated per material
number across all BOM occurrences** before it is checked against stock (the
legacy script counted the same stock as "available" separately for each
occurrence).

## Running it

```
pip install -r requirements.txt
python main.py
```

The tool walks you through file selection dialogs:
1. BOM file (Windchill export, sheet "Default")
2. SAP stock list
3. Order list (only uses the sheet starting with "ORDER LIST")
4. Optional: up to 3 additional storage files (dialog, "Cancel" to skip)
5. Number of units (default: 1)

Output: `Inventory_Report_*.xlsx` (dashboard, assembly status with a
visual BOM hierarchy, action required, availability by material, hidden
reference data) and `Inventory_Dashboard_*.html` (quick status overview
without opening Excel), both written to the folder of the BOM file.

## Core business rules (see config/mapping.yaml for the column mappings)

- **Material key**: canonical material number (usually 15 characters); SAP
  certificate number ("Zeugnis-Nr") = last 10 characters, or the number
  itself if it already has <=10 characters. No fuzzy/substring matching -
  numbers that cannot be uniquely resolved are flagged instead of guessed.
- **Demand** per material = sum(qty/unit) across all BOM rows with that
  material number, multiplied by the number of units - EXCLUDING any
  occurrence that sits underneath an assembly delivered as a complete,
  externally-procured unit (see below). This exclusion is per BOM row, not
  per material: the same material can carry genuine demand from one
  occurrence while being fully covered in another.
- **Availability** per material = sum of stock across all provided storage
  sources (SAP block flags are deliberately ignored, per the requirements
  discussion).
- **Status**: OK / PARTIAL / MISSING / COVERED per part (COVERED = zero net
  demand because a purchased-complete ancestor assembly already includes
  it); assembly status is derived bottom-up from the part statuses in its
  subtree (READY TO BUILD / PARTIAL BUILD / BLOCKED), unless the assembly
  is itself ordered as a complete unit, in which case it is
  ORDERED (EXTERNAL) and its own procurement status is what counts - not
  its components' statuses.
- **External procurement rule**: if an assembly's material number is found
  anywhere in the order list (plain presence check), it is treated as
  delivered as a complete unit. Every material underneath it - to any
  nesting depth, including inside further sub-assemblies - is "covered"
  and not separately demanded. If not found, the assembly is assumed to be
  built in-house from its BOM children (the original bottom-up logic).
- **Order list** otherwise only supplies context (supplier/date/status) for
  shortages - open orders do not count as available stock.
- **Main warehouse (DECMS)**: when the order list marks "demand covered" as
  OK and carries a stock figure, that counts as additional available
  stock. The special value 9999 ("hardware, practically always enough
  available") is capped at the material's actual demand rather than shown
  as a misleading raw number.
- **Location** (Assembly Status sheet): a 3-way category - Dummy Warehouse
  (SAP stock), Main Warehouse (DECMS), or Lift (material has a KANBAN
  number in the order list, which takes priority over any matched stock
  source).

## Flexible storage sources

`config/mapping.yaml` contains a storage-source registry. SAP has a fixed
column mapping; additional storage sources are read via generic column
detection (`src/loaders.load_generic_storage_source`). If a source is
missing or its structure is not recognized, it is skipped (with a
warning) instead of aborting the whole run. Once the structure of another
storage list is known, it can be added as a fixed entry with its own
column mapping in `mapping.yaml`.

## Known limits of the MVP / next steps

- Granularity only at BOM level, not yet per assembly step.
- No history/diff between two runs (next stage).
- No safety-stock/reorder-point logic.
- Exotic material numbers outside the expected pattern are not resolved
  automatically (per the requirements discussion: a rare edge case,
  manual review).

## Tests

```
pip install -r requirements-dev.txt
pytest
```
