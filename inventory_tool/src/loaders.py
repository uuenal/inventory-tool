"""Loading BOM, SAP stock, additional storage sources, and the order list.

Each loader is independently fault-tolerant: a missing or unmappable source
does not abort the whole pipeline, it raises a clear warning so main.py can
decide whether to continue without that source (core requirement: the tool
must work sensibly even with fewer than 4 storage files).
"""

import re
import warnings

import pandas as pd

from .material_keys import material_keys, normalize_material_number

warnings.filterwarnings("ignore")


class LoaderWarning:
    def __init__(self, source, message):
        self.source = source
        self.message = message

    def __str__(self):
        return f"[{self.source}] {self.message}"


def _find_column(columns, candidates):
    lower_map = {str(c).strip().lower(): c for c in columns}
    for cand in candidates:
        key = str(cand).strip().lower()
        if key in lower_map:
            return lower_map[key]
    return None


def _pick_sheet(xl, candidates):
    for cand in candidates:
        if cand in xl.sheet_names:
            return cand
    return xl.sheet_names[0] if xl.sheet_names else None


# ---------------------------------------------------------------------------
# BOM
# ---------------------------------------------------------------------------

def _make_unique_headers(values):
    headers = []
    used = {}
    for i, v in enumerate(values):
        name = "" if v is None else str(v).strip()
        if not name or name.lower() == "nan":
            name = f"Unnamed_{i}"
        if name in used:
            used[name] += 1
            name = f"{name}_{used[name]}"
        else:
            used[name] = 0
        headers.append(name)
    return headers


def _detect_header_row(raw_df, required_candidate_lists, max_scan_rows=15):
    """Finds the row that contains all required columns (e.g. Level/Number/Quantity).

    Some Windchill exports have a title or metadata row before the actual
    header row - so row 0 is not blindly assumed, the matching row is
    searched for instead (same approach as for the order list).
    """
    for i in range(min(max_scan_rows, len(raw_df))):
        vals = raw_df.iloc[i].tolist()
        col_names = {str(v).strip().lower() for v in vals if v is not None}
        if all(
            any(str(c).strip().lower() in col_names for c in candidates)
            for candidates in required_candidate_lists
        ):
            return i
    return None


def load_bom(file_path, mapping):
    cfg = mapping["bom"]
    xl = pd.ExcelFile(file_path)
    sheet = _pick_sheet(xl, cfg["sheet_name_candidates"])
    raw = pd.read_excel(xl, sheet_name=sheet, header=None)

    required_candidates = [cfg["columns"]["level"], cfg["columns"]["number"], cfg["columns"]["quantity"]]
    header_row = _detect_header_row(raw, required_candidates)
    if header_row is None:
        raise ValueError(
            f"BOM file: could not find a header row with Level/Number/Quantity. "
            f"Sheet used: '{sheet}' (available sheets: {xl.sheet_names}). "
            f"First row in sheet: {raw.iloc[0].tolist() if len(raw) else '(empty)'}"
        )

    headers = _make_unique_headers(raw.iloc[header_row].tolist())
    df = raw.iloc[header_row + 1:].copy()
    df.columns = headers
    df = df.reset_index(drop=True)

    col_level = _find_column(df.columns, cfg["columns"]["level"])
    col_number = _find_column(df.columns, cfg["columns"]["number"])
    col_title = _find_column(df.columns, cfg["columns"]["title"])
    col_qty = _find_column(df.columns, cfg["columns"]["quantity"])
    col_critical = _find_column(df.columns, cfg["columns"]["critical"])

    missing = [name for name, col in [
        ("level", col_level), ("number", col_number), ("quantity", col_qty)
    ] if col is None]
    if missing:
        raise ValueError(
            f"BOM file: required columns not found: {missing}. "
            f"Sheet used: '{sheet}', header row: {header_row + 1}. "
            f"Columns found: {list(df.columns)}"
        )

    out = pd.DataFrame({
        "Level": pd.to_numeric(df[col_level], errors="coerce"),
        "Material_Number_Raw": df[col_number].astype(str).str.strip(),
        "Title": df[col_title].astype(str).str.strip() if col_title else "",
        "Quantity_Per_Unit": pd.to_numeric(df[col_qty], errors="coerce").fillna(1),
        "Critical": df[col_critical] if col_critical else "",
    })
    out = out.dropna(subset=["Level"]).reset_index(drop=True)
    out["Material_Number"] = out["Material_Number_Raw"].apply(normalize_material_number)
    out = out[out["Material_Number"] != ""].reset_index(drop=True)
    out["Row_Order"] = range(len(out))

    # An item is an assembly if the next row sits deeper in the hierarchy
    # (same approach as the legacy script - proven to work).
    item_types = []
    for idx in range(len(out)):
        current_level = out.loc[idx, "Level"]
        is_assembly = False
        if idx < len(out) - 1 and out.loc[idx + 1, "Level"] > current_level:
            is_assembly = True
        item_types.append("Assembly" if is_assembly else "Part")
    out["Item_Type"] = item_types

    # Windchill's "Quantity" is per IMMEDIATE parent, not per top-level
    out["Quantity_Per_Unit"] = _rollup_quantity_per_unit(out["Level"].tolist(), out["Quantity_Per_Unit"].tolist())

    return out


def _rollup_quantity_per_unit(levels, qty_per_parent):
    """Cumulative quantity per ONE unit of the top-level assembly.

    Windchill's exported "Quantity" is the quantity per immediate parent
    occurrence, not already multiplied up to the top level - confirmed
    against a real multi-level BOM export (an assembly used 2x in its
    parent, whose own children each carry their per-that-assembly
    quantity, not the doubled total). A part nested inside a sub-assembly
    that is itself used N times must have its quantity multiplied by N,
    and this compounds across however many nested levels sit in between.

    Stack-based roll-up (same principle as the legacy script's qpu
    calculation): each row's cumulative quantity = its own per-parent
    quantity x the cumulative quantity of the nearest shallower ancestor
    still on the stack. The top-level row (nothing shallower) is 1 unit.
    """
    n = len(levels)
    qpu = [0.0] * n
    stack = []  # row indices, kept so their levels increase bottom-to-top
    for i in range(n):
        while stack and levels[stack[-1]] >= levels[i]:
            stack.pop()
        if not stack:
            qpu[i] = 1.0
        else:
            qpu[i] = (qty_per_parent[i] or 0.0) * qpu[stack[-1]]
        stack.append(i)
    return qpu


# ---------------------------------------------------------------------------
# SAP + generic storage sources
# ---------------------------------------------------------------------------

def load_mapped_storage_source(file_path, source_cfg):
    """For sources with a known mapping (currently: SAP)."""
    xl = pd.ExcelFile(file_path)
    sheet = _pick_sheet(xl, source_cfg["sheet_name_candidates"])
    raw = pd.read_excel(xl, sheet_name=sheet, header=None)

    required_candidates = [source_cfg["columns"]["material_key"], source_cfg["columns"]["quantity"]]
    header_row = _detect_header_row(raw, required_candidates)
    if header_row is None:
        raise ValueError(
            f"{source_cfg['label']}: could not find a header row with material key/quantity. "
            f"Sheet used: '{sheet}' (available sheets: {xl.sheet_names}). "
            f"First row in sheet: {raw.iloc[0].tolist() if len(raw) else '(empty)'}"
        )

    headers = _make_unique_headers(raw.iloc[header_row].tolist())
    df = raw.iloc[header_row + 1:].copy()
    df.columns = headers
    df = df.reset_index(drop=True)

    col_key = _find_column(df.columns, source_cfg["columns"]["material_key"])
    col_qty = _find_column(df.columns, source_cfg["columns"]["quantity"])
    col_loc = _find_column(df.columns, source_cfg["columns"].get("location", []))
    col_zeugnis = _find_column(df.columns, source_cfg["columns"].get("zeugnis_nr", []))

    if not col_key or not col_qty:
        raise ValueError(
            f"{source_cfg['label']}: required columns (material key/quantity) not found. "
            f"Sheet used: '{sheet}', header row: {header_row + 1}. "
            f"Columns found: {list(df.columns)}"
        )

    out = pd.DataFrame({
        "Material_Key_Raw": df[col_key].astype(str).str.strip(),
        "Quantity": pd.to_numeric(df[col_qty], errors="coerce").fillna(0),
        "Location": df[col_loc].astype(str).str.strip() if col_loc else "",
    })
    out["Source"] = source_cfg["label"]
    out["Material_Key"] = out["Material_Key_Raw"].apply(normalize_material_number)

    # Dummy-material rows (e.g. SHIELD prototype stock parked under one
    # shared SAP material number) carry their real part identity in the
    # certificate-number column instead - use that as the match key for
    # those rows only, direct material number for everything else.
    dummy = source_cfg.get("dummy_material")
    if dummy and col_zeugnis:
        dummy = normalize_material_number(dummy)
        zeugnis = df[col_zeugnis].astype(str).str.strip().apply(normalize_material_number)
        is_dummy = out["Material_Key"] == dummy
        out["Material_Key"] = out["Material_Key"].where(~is_dummy, zeugnis)

    out = out[(out["Material_Key"] != "") & (out["Quantity"] > 0)].reset_index(drop=True)
    return out[["Material_Key", "Quantity", "Location", "Source"]]


_GENERIC_KEY_CANDIDATES = [
    "Zeugnis-Nr", "Materialnummer", "Material Number", "Material", "Teilenummer",
    "Part Number", "Part ID", "Number", "Artikelnummer",
]
_GENERIC_QTY_CANDIDATES = [
    "Gesamtbestand", "Bestand", "Menge", "Quantity", "Stock", "Available Quantity",
    "Verfuegbare Menge", "Verfügbare Menge",
]
_GENERIC_LOC_CANDIDATES = ["Lagerplatz", "Lagerort", "Location", "Storage", "Lager"]


def load_generic_storage_source(file_path, source_label):
    """For storage sources without a known mapping: heuristic column detection.

    Returns (DataFrame, warnings). If no plausible column is found, the
    DataFrame is empty and a warning explains why - the source is then
    skipped by main.py instead of aborting the whole tool.
    """
    warns = []
    try:
        xl = pd.ExcelFile(file_path)
        sheet = xl.sheet_names[0]
        raw = pd.read_excel(xl, sheet_name=sheet, header=None)
    except Exception as exc:
        return pd.DataFrame(columns=["Material_Key", "Quantity", "Location", "Source"]), \
            [LoaderWarning(source_label, f"Could not read file: {exc}")]

    header_row = _detect_header_row(raw, [_GENERIC_KEY_CANDIDATES, _GENERIC_QTY_CANDIDATES])
    if header_row is None:
        warns.append(LoaderWarning(
            source_label,
            "Could not find a header row with material key/quantity "
            f"(sheet '{sheet}', first row: {raw.iloc[0].tolist() if len(raw) else '(empty)'}). "
            "Source is skipped, please extend the mapping in config/mapping.yaml."
        ))
        return pd.DataFrame(columns=["Material_Key", "Quantity", "Location", "Source"]), warns

    headers = _make_unique_headers(raw.iloc[header_row].tolist())
    df = raw.iloc[header_row + 1:].copy()
    df.columns = headers
    df = df.reset_index(drop=True)

    col_key = _find_column(df.columns, _GENERIC_KEY_CANDIDATES)
    col_qty = _find_column(df.columns, _GENERIC_QTY_CANDIDATES)
    col_loc = _find_column(df.columns, _GENERIC_LOC_CANDIDATES)

    if not col_key or not col_qty:
        warns.append(LoaderWarning(
            source_label,
            "Could not automatically detect a material key or quantity column "
            f"(columns found: {list(df.columns)}). Source is skipped, please "
            "extend the mapping in config/mapping.yaml."
        ))
        return pd.DataFrame(columns=["Material_Key", "Quantity", "Location", "Source"]), warns

    out = pd.DataFrame({
        "Material_Key_Raw": df[col_key].astype(str).str.strip(),
        "Quantity": pd.to_numeric(df[col_qty], errors="coerce").fillna(0),
        "Location": df[col_loc].astype(str).str.strip() if col_loc else "",
    })
    out["Source"] = source_label
    out["Material_Key"] = out["Material_Key_Raw"].apply(normalize_material_number)
    out = out[(out["Material_Key"] != "") & (out["Quantity"] > 0)].reset_index(drop=True)
    return out[["Material_Key", "Quantity", "Location", "Source"]], warns


# ---------------------------------------------------------------------------
# Order List
# ---------------------------------------------------------------------------

def _row_values(raw_df, row_idx):
    return raw_df.iloc[row_idx].tolist()


def _find_col_idx(values, label, exact=True):
    target = label.strip().lower()
    for idx, v in enumerate(values):
        if v is None:
            continue
        s = str(v).strip().lower()
        if (s == target) if exact else s.startswith(target):
            return idx
    return None


def load_order_list(file_path, mapping):
    cfg = mapping["order_list"]
    xl = pd.ExcelFile(file_path)
    sheet = next((s for s in xl.sheet_names if s.startswith(cfg["sheet_name_prefix"])), None)
    if sheet is None:
        raise ValueError(
            f"No sheet found starting with '{cfg['sheet_name_prefix']}'. "
            f"Available sheets: {xl.sheet_names}"
        )

    raw = pd.read_excel(xl, sheet_name=sheet, header=None)

    header_row_idx = None
    for i in range(min(cfg["header_scan_rows"], len(raw))):
        vals = _row_values(raw, i)
        if _find_col_idx(vals, "Part ID") is not None and _find_col_idx(vals, "delivery status") is not None:
            header_row_idx = i
            break
    if header_row_idx is None:
        raise ValueError(f"Order list ('{sheet}'): could not detect the header row.")

    header_vals = _row_values(raw, header_row_idx)

    def col(label, exact=True):
        return _find_col_idx(header_vals, label, exact=exact)

    idx_part_id = col(cfg["columns"]["part_id"][0])
    idx_sap_id = col(cfg["columns"]["sap_id"][0])
    idx_kanban = col(cfg["columns"]["kanban"][0])
    idx_supplier = col(cfg["columns"]["supplier"][0])
    idx_order_qty = col(cfg["columns"]["order_qty"][0], exact=False)
    idx_delivery_status = col(cfg["columns"]["delivery_status"][0])
    idx_delivery_date = None
    for cand in cfg["columns"]["delivery_date"]:
        idx_delivery_date = col(cand, exact=False)
        if idx_delivery_date is not None:
            break
    idx_demand_per_unit = col(cfg["demand_per_unit_label_prefix"], exact=False)
    idx_order_number = col(cfg["columns"]["order_number"][0])
    idx_demand_covered = col(cfg["columns"]["demand_covered"][0])
    idx_decms_stock = col(cfg["columns"]["decms_stock"][0], exact=False)

    if idx_part_id is None:
        raise ValueError(f"Order list ('{sheet}'): column 'Part ID' not found.")

    # Data rows start where the Part ID column looks like a material code
    # (instead of a fixed row offset - robust against extra/missing sub-header rows).
    material_pattern = re.compile(r"^[A-Z0-9][A-Z0-9._-]{5,}$")
    data_start = None
    for i in range(header_row_idx + 1, len(raw)):
        val = raw.iat[i, idx_part_id]
        if val is not None and material_pattern.match(str(val).strip().upper()):
            data_start = i
            break
    if data_start is None:
        raise ValueError(f"Order list ('{sheet}'): no data rows found after the header row.")

    data = raw.iloc[data_start:].reset_index(drop=True)

    def safe_col(idx):
        return data[idx] if idx is not None else pd.Series([None] * len(data))

    def to_str(series):
        return series.apply(lambda v: "" if v is None or (isinstance(v, float) and pd.isna(v)) else str(v).strip())

    def to_date_str(series):
        def fmt(v):
            if v is None or (isinstance(v, float) and pd.isna(v)):
                return ""
            if hasattr(v, "strftime"):
                return v.strftime("%Y-%m-%d")
            return str(v).strip()
        return series.apply(fmt)

    out = pd.DataFrame({
        "Part_ID_Raw": to_str(safe_col(idx_part_id)),
        "SAP_ID": to_str(safe_col(idx_sap_id)),
        "KANBAN": to_str(safe_col(idx_kanban)),
        "Supplier": to_str(safe_col(idx_supplier)),
        "Order_Qty": pd.to_numeric(safe_col(idx_order_qty), errors="coerce"),
        "Delivery_Date": to_date_str(safe_col(idx_delivery_date)),
        "Delivery_Status": to_str(safe_col(idx_delivery_status)),
        "Demand_Per_Unit": pd.to_numeric(safe_col(idx_demand_per_unit), errors="coerce"),
        "Order_Number": to_str(safe_col(idx_order_number)),
        "Demand_Covered": to_str(safe_col(idx_demand_covered)),
        "DECMS_Stock": pd.to_numeric(safe_col(idx_decms_stock), errors="coerce"),
    })
    out = out[out["Part_ID_Raw"] != ""].reset_index(drop=True)
    out["Material_Number"] = out["Part_ID_Raw"].apply(normalize_material_number)
    out["Zeugnis_Nr"] = out["Material_Number"].apply(lambda n: material_keys(n)["zeugnis_nr"])
    return out


# ---------------------------------------------------------------------------
# CSP Hall (external request/buffer-area log)
# ---------------------------------------------------------------------------

def load_csp_hall(file_path, mapping):
    cfg = mapping["csp_hall"]
    xl = pd.ExcelFile(file_path)
    sheet = _pick_sheet(xl, cfg["sheet_name_candidates"])
    raw = pd.read_excel(xl, sheet_name=sheet, header=None)

    required_candidates = [cfg["columns"]["part_number"], cfg["columns"]["quantity"], cfg["columns"]["status"]]
    header_row = _detect_header_row(raw, required_candidates)
    if header_row is None:
        raise ValueError(
            f"CSP Hall file: could not find a header row with Part number/Qty/Status. "
            f"Sheet used: '{sheet}' (available sheets: {xl.sheet_names}). "
            f"First row in sheet: {raw.iloc[0].tolist() if len(raw) else '(empty)'}"
        )

    headers = _make_unique_headers(raw.iloc[header_row].tolist())
    df = raw.iloc[header_row + 1:].copy()
    df.columns = headers
    df = df.reset_index(drop=True)

    col_part = _find_column(df.columns, cfg["columns"]["part_number"])
    col_qty = _find_column(df.columns, cfg["columns"]["quantity"])
    col_status = _find_column(df.columns, cfg["columns"]["status"])
    col_desc = _find_column(df.columns, cfg["columns"].get("description", []))
    col_date = _find_column(df.columns, cfg["columns"].get("request_date", []))
    col_note = _find_column(df.columns, cfg["columns"].get("note", []))

    missing = [name for name, col in [
        ("part_number", col_part), ("quantity", col_qty), ("status", col_status)
    ] if col is None]
    if missing:
        raise ValueError(
            f"CSP Hall file: required columns not found: {missing}. "
            f"Sheet used: '{sheet}', header row: {header_row + 1}. "
            f"Columns found: {list(df.columns)}"
        )

    out = pd.DataFrame({
        "Material_Number_Raw": df[col_part].astype(str).str.strip(),
        "Quantity": pd.to_numeric(df[col_qty], errors="coerce").fillna(0),
        "Status": df[col_status].astype(str).str.strip(),
        "Description": df[col_desc].astype(str).str.strip() if col_desc else "",
        "Request_Date": df[col_date] if col_date else "",
        "Note": df[col_note].astype(str).str.strip() if col_note else "",
    })
    out["Material_Number"] = out["Material_Number_Raw"].apply(normalize_material_number)
    out = out[out["Material_Number"] != ""].reset_index(drop=True)
    return out
