"""Excel output: dashboard first, action-required prominent, reference data
hidden, native Excel tables with filters instead of plain data dumps.

Directly addresses the review feedback on the legacy script: there, all 7
sheets were equally prominent, raw reference data dominated the file, and
there was no summary and no filters/freeze panes.
"""

from datetime import datetime

import pandas as pd
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.utils.dataframe import dataframe_to_rows
from openpyxl.worksheet.table import Table, TableStyleInfo

ABB_RED = "FF000F"
HEADER_FILL = PatternFill(start_color=ABB_RED, end_color=ABB_RED, fill_type="solid")
HEADER_FONT = Font(bold=True, color="FFFFFF", size=11)
TITLE_FONT = Font(bold=True, size=16, color=ABB_RED)
KPI_LABEL_FONT = Font(bold=True, size=11)
KPI_VALUE_FONT = Font(bold=True, size=20)
PROTOTYPE_LABEL_FONT = Font(bold=True, size=13, color="FFFFFF")
PROTOTYPE_VALUE_FONT = Font(bold=True, size=40, color="FFFFFF")
PROTOTYPE_FILL = PatternFill(start_color=ABB_RED, end_color=ABB_RED, fill_type="solid")
OK_FILL = PatternFill(start_color="C6EFCE", end_color="C6EFCE", fill_type="solid")
PARTIAL_FILL = PatternFill(start_color="FFEB9C", end_color="FFEB9C", fill_type="solid")
MISSING_FILL = PatternFill(start_color="FFC7CE", end_color="FFC7CE", fill_type="solid")
NA_FILL = PatternFill(start_color="E7E6E6", end_color="E7E6E6", fill_type="solid")

STATUS_FILLS = {
    "OK": OK_FILL,
    "READY TO BUILD": OK_FILL,
    "COVERED": OK_FILL,
    "ORDERED (EXTERNAL)": OK_FILL,
    "PARTIAL": PARTIAL_FILL,
    "PARTIAL BUILD": PARTIAL_FILL,
    "MISSING": MISSING_FILL,
    "BLOCKED": MISSING_FILL,
    "N/A": NA_FILL,
}

# Purely visual: one subtle, monochromatic blue-gray tone per BOM hierarchy
# depth, darkening slightly with depth. Deliberately not traffic-light
# colors - these carry no status meaning, they only help the eye follow
# the existing Level value across the H0-H4 staircase columns.
HIERARCHY_MAX_COLUMN = 4
HIERARCHY_FILLS = [
    PatternFill(start_color=c, end_color=c, fill_type="solid")
    for c in ("E9EDF2", "DBE2EA", "CDD7E3", "BFCCDC", "B1C1D5")
]


def _auto_fit(ws, max_width=50):
    for column in ws.columns:
        max_length = 0
        col_letter = column[0].column_letter
        for cell in column:
            val = "" if cell.value is None else str(cell.value)
            max_length = max(max_length, len(val))
        ws.column_dimensions[col_letter].width = min(max_length + 2, max_width)


def _write_table(ws, df, table_name, status_col_name=None, start_row=1):
    """Writes a DataFrame as a native Excel table (filters included)."""
    for r_idx, row in enumerate(dataframe_to_rows(df, index=False, header=True), start_row):
        ws.append(row)
        if r_idx == start_row:
            for cell in ws[r_idx]:
                cell.fill = HEADER_FILL
                cell.font = HEADER_FONT

    end_row = start_row + len(df)
    end_col = len(df.columns)
    if end_row > start_row and end_col > 0:
        ref = f"A{start_row}:{get_column_letter(end_col)}{end_row}"
        table = Table(displayName=table_name, ref=ref)
        table.tableStyleInfo = TableStyleInfo(
            name="TableStyleMedium2", showRowStripes=True, showFirstColumn=False
        )
        ws.add_table(table)

    if status_col_name and status_col_name in df.columns:
        status_col_idx = df.columns.get_loc(status_col_name) + 1
        for r_idx in range(start_row + 1, end_row + 1):
            val = str(ws.cell(row=r_idx, column=status_col_idx).value or "").strip()
            fill = STATUS_FILLS.get(val)
            if fill:
                ws.cell(row=r_idx, column=status_col_idx).fill = fill

    ws.freeze_panes = ws.cell(row=start_row + 1, column=1)
    _auto_fit(ws)


def _hierarchy_columns(levels, max_col=HIERARCHY_MAX_COLUMN):
    """Purely a display transform of the existing Level values - no new
    hierarchy is computed. Each row's Level number is placed into the one
    H-column matching its depth; every other H-column for that row is left
    blank, producing the staircase indentation. A Level deeper than
    max_col still shows its real, un-clipped number - only the COLUMN it
    lands in is capped at the last one, so nothing is lost and no extra
    column is added.
    """
    columns = {i: [] for i in range(max_col + 1)}
    for lvl in levels:
        lvl_int = int(lvl) if pd.notna(lvl) else None
        target = min(lvl_int, max_col) if lvl_int is not None else None
        for i in range(max_col + 1):
            columns[i].append(lvl_int if i == target else None)
    return columns


def _apply_hierarchy_formatting(ws, level_col_idx, hierarchy_start_col, num_rows, header_row=1):
    """Post-processing pass for the '2. Assembly Status' sheet: colors the
    H0-H4 staircase cells, narrows those columns, and collapses the
    original Level column into an Excel column group (still present,
    still exportable/expandable, just not in the way by default)."""
    for r_idx in range(header_row + 1, header_row + 1 + num_rows):
        for h in range(HIERARCHY_MAX_COLUMN + 1):
            cell = ws.cell(row=r_idx, column=hierarchy_start_col + h)
            if cell.value is not None:
                cell.fill = HIERARCHY_FILLS[h]
                cell.font = Font(bold=True)
                cell.alignment = Alignment(horizontal="center")

    for h in range(HIERARCHY_MAX_COLUMN + 1):
        ws.column_dimensions[get_column_letter(hierarchy_start_col + h)].width = 4

    level_letter = get_column_letter(level_col_idx)
    ws.column_dimensions[level_letter].outlineLevel = 1
    ws.column_dimensions[level_letter].hidden = True
    ws.sheet_properties.outlinePr.summaryRight = True


def _kpi_cell(ws, row, col, label, value, fill=None):
    label_cell = ws.cell(row=row, column=col, value=label)
    label_cell.font = KPI_LABEL_FONT
    value_cell = ws.cell(row=row + 1, column=col, value=value)
    value_cell.font = KPI_VALUE_FONT
    if fill:
        value_cell.fill = fill
        value_cell.alignment = Alignment(horizontal="center")


def _write_prototype_banner(ws, row, num_units):
    """Big, unmissable "PROTOTYPE QUANTITY" banner - the number every other
    figure on this dashboard is relative to, so it has to be the first
    thing anyone sees, not a small subtitle line."""
    for r, height in ((row, 20), (row + 1, 50)):
        ws.row_dimensions[r].height = height
        for col in range(1, 5):
            ws.cell(row=r, column=col).fill = PROTOTYPE_FILL

    label_cell = ws.cell(row=row, column=1, value="PROTOTYPE QUANTITY")
    label_cell.font = PROTOTYPE_LABEL_FONT
    label_cell.alignment = Alignment(horizontal="center", vertical="center")
    ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=4)

    value_cell = ws.cell(row=row + 1, column=1, value=num_units)
    value_cell.font = PROTOTYPE_VALUE_FONT
    value_cell.alignment = Alignment(horizontal="center", vertical="center")
    ws.merge_cells(start_row=row + 1, start_column=1, end_row=row + 1, end_column=4)


def _write_dashboard(ws, stats, num_units, generated_at):
    ws["A1"] = "INVENTORY MANAGEMENT DASHBOARD"
    ws["A1"].font = TITLE_FONT
    ws["A2"] = f"Generated: {generated_at}"
    ws["A2"].font = Font(italic=True, size=10)

    _write_prototype_banner(ws, 4, num_units)

    labels = [
        ("Total Parts", stats["parts_total"], None),
        ("Parts OK", stats["parts_ok"], OK_FILL),
        ("Parts Partial", stats["parts_partial"], PARTIAL_FILL),
        ("Parts Missing", stats["parts_missing"], MISSING_FILL),
    ]
    for i, (label, value, fill) in enumerate(labels):
        _kpi_cell(ws, 8, 1 + i * 2, label, value, fill)

    labels2 = [
        ("Assemblies Ready", stats["asm_ready"], OK_FILL),
        ("Assemblies Partial", stats["asm_partial"], PARTIAL_FILL),
        ("Assemblies Blocked", stats["asm_blocked"], MISSING_FILL),
        ("Critical Parts Missing", stats["critical_missing"], MISSING_FILL),
    ]
    for i, (label, value, fill) in enumerate(labels2):
        _kpi_cell(ws, 11, 1 + i * 2, label, value, fill)

    ws["A14"] = "Storage sources used:"
    ws["A14"].font = KPI_LABEL_FONT
    ws["A15"] = stats["sources_used"]
    if stats.get("sources_skipped"):
        ws["A16"] = "Skipped/not provided sources:"
        ws["A16"].font = KPI_LABEL_FONT
        ws["A17"] = stats["sources_skipped"]

    for col in range(1, 9):
        ws.column_dimensions[get_column_letter(col)].width = 18


def create_report(output_path, num_units, bom_view, availability_df, gap_df,
                   order_df, sap_df, sources_used, sources_skipped):
    wb = Workbook()
    wb.remove(wb.active)
    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M")

    part_ok = int((availability_df["Status"] == "OK").sum())
    part_partial = int((availability_df["Status"] == "PARTIAL").sum())
    part_missing = int((availability_df["Status"] == "MISSING").sum())
    asm = bom_view[bom_view["Item_Type"] == "Assembly"]
    stats = {
        "parts_total": len(availability_df),
        "parts_ok": part_ok,
        "parts_partial": part_partial,
        "parts_missing": part_missing,
        "asm_ready": int((asm["Assembly_Readiness"] == "READY TO BUILD").sum()),
        "asm_partial": int((asm["Assembly_Readiness"] == "PARTIAL BUILD").sum()),
        "asm_blocked": int((asm["Assembly_Readiness"] == "BLOCKED").sum()),
        "critical_missing": int(((availability_df["Status"] == "MISSING") &
                                  (availability_df["Critical"].astype(str).str.lower() == "yes")).sum()),
        "sources_used": ", ".join(sources_used) if sources_used else "none",
        "sources_skipped": ", ".join(sources_skipped) if sources_skipped else "",
    }

    ws_dash = wb.create_sheet("1. Dashboard")
    _write_dashboard(ws_dash, stats, num_units, generated_at)

    ws_bom = wb.create_sheet("2. Assembly Status")
    bom_display = bom_view[[
        "Level", "Item_Type", "Material_Number", "Title", "Quantity_Per_Unit",
        "Status", "Assembly_Readiness", "Ordered", "Available_Qty",
        "Available_Qty", "Delivery_Date", "Location", "Comment"
    ]].copy()
    bom_display.columns = [
        "Level", "Type", "Material Number", "Description", "Qty/Unit",
        "Status (Part)", "Assembly Status", "Ordered", "Availability in House",
        "Quantity in House", "Delivery Date", "Location", "Comment"
    ]
    bom_display["Quantity in House"] = pd.to_numeric(
        bom_display["Quantity in House"], errors="coerce"
    ).fillna(0)
    bom_display["Availability in House"] = bom_display["Availability in House"].apply(
        lambda v: "Yes" if (v is not None and not pd.isna(v) and v > 0) else "No"
    )

    # Purely visual staircase columns (H0-H4) placed ahead of everything
    # else - built only from the existing Level column, row order and the
    # Level values themselves are untouched.
    hierarchy_df = pd.DataFrame(
        {f"H{i}": vals for i, vals in _hierarchy_columns(bom_display["Level"]).items()}
    )
    bom_display = pd.concat([hierarchy_df.reset_index(drop=True), bom_display.reset_index(drop=True)], axis=1)

    _write_table(ws_bom, bom_display, "AssemblyStatus")

    # Two-purpose status column: part status OR assembly readiness, depending on the row.
    status_col = bom_display.columns.get_loc("Status (Part)") + 1
    readiness_col = bom_display.columns.get_loc("Assembly Status") + 1
    for r_idx in range(2, len(bom_display) + 2):
        part_val = str(ws_bom.cell(row=r_idx, column=status_col).value or "").strip()
        asm_val = str(ws_bom.cell(row=r_idx, column=readiness_col).value or "").strip()
        fill = STATUS_FILLS.get(part_val) or STATUS_FILLS.get(asm_val)
        if fill:
            ws_bom.cell(row=r_idx, column=status_col).fill = fill
            ws_bom.cell(row=r_idx, column=readiness_col).fill = fill

    _apply_hierarchy_formatting(
        ws_bom,
        level_col_idx=bom_display.columns.get_loc("Level") + 1,
        hierarchy_start_col=bom_display.columns.get_loc("H0") + 1,
        num_rows=len(bom_display),
    )

    ws_action = wb.create_sheet("3. ACTION REQUIRED")
    if not gap_df.empty:
        # Sort by the underlying Critical/Status fields first, then drop
        # Critical from the display - critical items still surface at the
        # top even though the column itself is no longer shown.
        gap_sorted = gap_df.sort_values(by=["Critical", "Status"], ascending=[False, True])
        action_display = gap_sorted[[
            "Material_Number", "Title", "Total_Demand", "Available_Qty",
            "Gap", "Status", "Order_Supplier", "Order_Number",
            "Order_Delivery_Status", "Order_Delivery_Date"
        ]].copy()
        action_display.columns = [
            "Material Number", "Description", "Demand", "Available",
            "Shortage", "Status", "Supplier", "Order Number",
            "Delivery Status", "Delivery Date"
        ]
        action_display = action_display.reset_index(drop=True)
        _write_table(ws_action, action_display, "ActionRequired", status_col_name="Status")
    else:
        ws_action["A1"] = "No open shortages."

    ws_avail = wb.create_sheet("4. Availability by Material")
    avail_display = availability_df[[
        "Material_Number", "Title", "KANBAN", "Usage_Count", "Total_Demand",
        "Available_Qty", "Gap", "Status", "Locations", "Sources"
    ]].copy()
    avail_display.columns = [
        "Material Number", "Description", "KANBAN", "BOM Occurrences", "Demand",
        "Available", "Shortage", "Status", "Locations", "Sources"
    ]
    _write_table(ws_avail, avail_display, "Availability", status_col_name="Status")

    # Reference data: kept for traceability, but hidden - nobody should have
    # to look at it first when opening the file.
    ws_bom_ref = wb.create_sheet("5. BOM Reference")
    bom_ref = bom_view[["Level", "Item_Type", "Material_Number", "Title", "Quantity_Per_Unit"]].copy()
    bom_ref.columns = ["Level", "Type", "Material Number", "Description", "Qty/Unit"]
    _write_table(ws_bom_ref, bom_ref, "BOMReference")
    ws_bom_ref.sheet_state = "hidden"

    if sap_df is not None and len(sap_df) > 0:
        ws_sap_ref = wb.create_sheet("6. SAP Reference")
        _write_table(ws_sap_ref, sap_df, "SAPReference")
        ws_sap_ref.sheet_state = "hidden"

    if order_df is not None and len(order_df) > 0:
        ws_order_ref = wb.create_sheet("7. Order List Reference")
        order_ref = order_df[[
            "Material_Number", "SAP_ID", "KANBAN", "Supplier", "Order_Qty",
            "Delivery_Date", "Delivery_Status"
        ]].copy()
        _write_table(ws_order_ref, order_ref, "OrderListReference")
        ws_order_ref.sheet_state = "hidden"

    wb.move_sheet("1. Dashboard", offset=-len(wb.sheetnames))
    wb.active = 0
    wb.save(output_path)
    return stats
