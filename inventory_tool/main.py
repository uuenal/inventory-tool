"""Inventory Management Tool - MVP.

Checks, for a Windchill BOM, whether all required materials are available
(stock across 1..n storage sources + context from the order list for
shortages). See README.md for the business background.

Usage: file selection via dialog windows (same as the legacy script).
"""

import os
import sys
from datetime import datetime

import tkinter as tk
import yaml
from tkinter import filedialog, simpledialog

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src import availability, excel_report, html_dashboard, loaders

CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config", "mapping.yaml")


def ask_for_file(title, required=True):
    root = tk.Tk()
    root.withdraw()
    root.lift()
    root.attributes("-topmost", True)
    path = filedialog.askopenfilename(
        title=title,
        filetypes=[("Excel files", "*.xlsx *.xls"), ("All files", "*.*")],
    )
    root.destroy()
    if required and not path:
        raise SystemExit(f"Cancelled: '{title}' was not selected.")
    if path:
        print(f"  Selected: {os.path.basename(path)}")
    return path or None


def ask_num_units(default=1):
    root = tk.Tk()
    root.withdraw()
    root.lift()
    root.attributes("-topmost", True)
    value = simpledialog.askinteger(
        "Number of Units",
        "For how many units (e.g. prototypes) should demand be calculated?",
        initialvalue=default, minvalue=1,
    )
    root.destroy()
    return value or default


def load_optional_storage_sources(mapping):
    """Asks for additional storage files beyond the mandatory SAP source.

    Core requirement: the tool must also work if not all storage files are
    available - so every additional file is optional, and cancelling the
    dialog is treated as "no further source".
    """
    extra_frames = []
    used, skipped = [], []
    extra_registry = [s for s in mapping["storage_sources"] if not s.get("required")]

    for source_cfg in extra_registry:
        path = ask_for_file(
            f"Optional: select {source_cfg['label']} (Cancel = skip)",
            required=False,
        )
        if not path:
            skipped.append(source_cfg["label"])
            continue
        df, warns = loaders.load_generic_storage_source(path, source_cfg["label"])
        for w in warns:
            print(f"WARNING: {w}")
        if len(df) > 0:
            extra_frames.append(df)
            used.append(source_cfg["label"])
        else:
            skipped.append(f"{source_cfg['label']} (structure not recognized)")

    return extra_frames, used, skipped


def run():
    print("=" * 80)
    print("INVENTORY MANAGEMENT TOOL - MVP")
    print("=" * 80)

    with open(CONFIG_PATH, encoding="utf-8") as f:
        mapping = yaml.safe_load(f)

    print("\nStep 1/4: Select required files - please choose in this order:")
    print("  1) BOM (Windchill export)")
    print("  2) SAP stock list")
    print("  3) Order list")
    bom_file = ask_for_file("1/3: Select BOM file (Windchill export)")
    sap_file = ask_for_file("2/3: Select SAP stock list")
    order_file = ask_for_file("3/3: Select order list")

    print("\nStep 2/4: Additional storage sources (optional, 'Cancel' to skip)")
    extra_stock_frames, extra_used, extra_skipped = load_optional_storage_sources(mapping)

    num_units = ask_num_units(default=1)

    print("\nStep 3/4: Loading and evaluating data...")
    bom_df = loaders.load_bom(bom_file, mapping)
    sap_cfg = next(s for s in mapping["storage_sources"] if s["id"] == "sap")
    sap_df = loaders.load_mapped_storage_source(sap_file, sap_cfg)
    order_df = loaders.load_order_list(order_file, mapping)

    # Per-BOM-row order context first (which rows are ordered, which sit
    # underneath an assembly delivered as a complete unit) - demand
    # aggregation depends on it.
    bom_df = availability.enrich_bom_with_order_context(bom_df, order_df)

    demand_df = availability.aggregate_demand(bom_df, num_units=num_units)
    decms_df = availability.extract_decms_stock(order_df, demand_df)
    stock_df = availability.combine_stock_sources([sap_df] + extra_stock_frames + [decms_df])
    avail_df = availability.compute_availability(demand_df, stock_df)
    avail_df = availability.attach_kanban(avail_df, order_df)
    avail_df = availability.enrich_gaps_with_order_list(avail_df, order_df)
    bom_view = availability.build_bom_view(bom_df, avail_df, stock_df=stock_df, sap_label=sap_cfg["label"])
    gap_df = avail_df[avail_df["Gap"] > 0].sort_values(
        by=["Critical", "Status"], ascending=[False, True]
    ).reset_index(drop=True)

    sources_used = [sap_cfg["label"]] + extra_used
    if len(decms_df) > 0:
        sources_used.append("DECMS (order list, main warehouse)")
    sources_skipped = extra_skipped

    print("\nStep 4/4: Generating report...")
    output_dir = os.path.dirname(bom_file)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    excel_path = os.path.join(output_dir, f"Inventory_Report_{num_units}_Units_{stamp}.xlsx")
    html_path = os.path.join(output_dir, f"Inventory_Dashboard_{num_units}_Units_{stamp}.html")

    stats = excel_report.create_report(
        excel_path, num_units, bom_view, avail_df, gap_df, order_df, sap_df,
        sources_used, sources_skipped,
    )
    html_dashboard.write_html(html_path, stats, num_units, gap_df)

    print("\n" + "=" * 80)
    print("DONE")
    print("=" * 80)
    print(f"Parts:      OK={stats['parts_ok']}  PARTIAL={stats['parts_partial']}  MISSING={stats['parts_missing']}")
    print(f"Assemblies: READY={stats['asm_ready']}  PARTIAL={stats['asm_partial']}  BLOCKED={stats['asm_blocked']}")
    print(f"Excel report:   {excel_path}")
    print(f"HTML dashboard: {html_path}")


if __name__ == "__main__":
    try:
        run()
    except Exception as exc:
        print(f"\nERROR: {exc}")
        raise
    input("\nPress Enter to exit...")
