"""Demand aggregation, availability/gap calculation, assembly readiness.

Core point (bugfix vs. the legacy script): demand is summed up PER MATERIAL
across ALL BOM occurrences BEFORE it is checked against stock. The legacy
script instead checked each BOM row individually against the full stock -
a material used in three assemblies had the same stock counted as
"available" three times over there.
"""

import pandas as pd

from .material_keys import material_keys


def _unique_join(values, sep=" | "):
    seen = set()
    result = []
    for v in values:
        s = str(v).strip()
        if s and s.lower() != "nan" and s not in seen:
            seen.add(s)
            result.append(s)
    return sep.join(result)


def aggregate_demand(bom_df, num_units=1):
    """Demand per material number, summed across all BOM occurrences.

    BOM occurrences that sit underneath an assembly which is itself
    delivered as a complete, externally-procured unit (see
    enrich_bom_with_order_context / "Covered_By") contribute ZERO to the
    sum - that specific occurrence's quantity is already physically part of
    the purchased assembly and must not be sourced separately. This is a
    per-ROW exclusion, not a per-material one: the same material can still
    have genuine demand from OTHER, non-covered occurrences elsewhere in
    the BOM (e.g. used both inside a purchased assembly and inside one that
    is built in-house) - only the covered occurrence's quantity drops out.

    "Covered_By" is optional for backward compatibility (e.g. callers that
    build a bom_df by hand, as the tests do): if the column is absent, no
    occurrence is treated as covered, exactly matching the previous
    behaviour.
    """
    parts = bom_df[bom_df["Item_Type"] == "Part"].copy()

    if "Covered_By" in parts.columns:
        is_covered = parts["Covered_By"].fillna("").astype(str).str.strip() != ""
    else:
        is_covered = pd.Series(False, index=parts.index)
    parts["Quantity_Per_Unit_Effective"] = parts["Quantity_Per_Unit"].where(~is_covered, 0)

    def is_critical(series):
        return "Yes" if any(str(x).strip().lower() in ("yes", "true", "1") for x in series) else "No"

    demand = parts.groupby("Material_Number", as_index=False).agg(
        Title=("Title", _unique_join),
        Quantity_Per_Unit_Sum=("Quantity_Per_Unit_Effective", "sum"),
        Critical=("Critical", is_critical),
        Usage_Count=("Material_Number", "count"),
    )
    demand["Zeugnis_Nr"] = demand["Material_Number"].apply(lambda n: material_keys(n)["zeugnis_nr"])
    demand["Total_Demand"] = demand["Quantity_Per_Unit_Sum"] * num_units
    return demand


def combine_stock_sources(storage_dfs):
    """Combines all loaded storage sources (1..n) into a single table."""
    frames = [df for df in storage_dfs if df is not None and len(df) > 0]
    if not frames:
        return pd.DataFrame(columns=["Material_Key", "Quantity", "Location", "Source"])
    return pd.concat(frames, ignore_index=True)


def _stock_lookup(stock_df):
    if stock_df.empty:
        return {}
    grouped = stock_df.groupby("Material_Key").agg(
        Quantity=("Quantity", "sum"),
        Location=("Location", _unique_join),
        Source=("Source", _unique_join),
    )
    return grouped.to_dict(orient="index")


def compute_availability(demand_df, stock_df):
    """Availability per material = sum of stock across all storage sources.

    Matching is done on the full material number OR the derived certificate
    number, depending on which key the given storage source uses. A set
    avoids double-counting for the edge case where both keys are identical
    (material number already <=10 characters).
    """
    lookup = _stock_lookup(stock_df)

    avail_qty, locations, sources = [], [], []
    for _, row in demand_df.iterrows():
        keys = {row["Material_Number"], row["Zeugnis_Nr"]}
        qty_sum, locs, srcs = 0, [], []
        for k in keys:
            if k in lookup:
                qty_sum += lookup[k]["Quantity"]
                if lookup[k]["Location"]:
                    locs.append(lookup[k]["Location"])
                if lookup[k]["Source"]:
                    srcs.append(lookup[k]["Source"])
        avail_qty.append(qty_sum)
        locations.append(_unique_join(locs))
        sources.append(_unique_join(srcs))

    out = demand_df.copy()
    out["Available_Qty"] = avail_qty
    out["Locations"] = locations
    out["Sources"] = sources
    out["Gap"] = (out["Total_Demand"] - out["Available_Qty"]).clip(lower=0)

    def status(r):
        # A material with zero net demand (fully covered by a purchased-
        # complete assembly, see aggregate_demand) is not "missing" just
        # because nobody happens to also hold physical stock of it - it
        # simply is not separately needed.
        if r["Total_Demand"] <= 0:
            return "COVERED"
        if r["Available_Qty"] <= 0:
            return "MISSING"
        if r["Gap"] > 0:
            return "PARTIAL"
        return "OK"

    out["Status"] = out.apply(status, axis=1)
    return out


DECMS_HARDWARE_SENTINEL = 9999
DECMS_SOURCE_LABEL = "main warehouse (DECMS)"


def _lookup_tables(order_df):
    by_number = order_df.drop_duplicates("Material_Number").set_index("Material_Number")
    by_zeugnis = order_df.drop_duplicates("Zeugnis_Nr").set_index("Zeugnis_Nr")
    return by_number, by_zeugnis


def _match_order_row(by_number, by_zeugnis, material_number, zeugnis_nr):
    if material_number in by_number.index:
        return by_number.loc[material_number]
    if zeugnis_nr in by_zeugnis.index:
        return by_zeugnis.loc[zeugnis_nr]
    return None


def enrich_bom_with_order_context(bom_df, order_df):
    """Per-BOM-ROW order context: is this row itself ordered, and does it
    sit underneath an assembly that is delivered as a complete unit?

    Business rule (confirmed): an assembly is treated as "ordered as a
    complete, externally-procured unit" purely by whether its material
    number appears ANYWHERE in the order list - a plain presence check,
    independent of any status/quantity column on that order-list row. If
    it is not found, it is assumed to be built in-house from its BOM
    children as before.

    When such an assembly is found, every material underneath it in the
    BOM - to any nesting depth, including inside further sub-assemblies -
    is "covered": it already physically arrives as part of the purchased
    unit and must not be separately demanded. This does NOT apply the
    other way around: an ordinary Part being individually present in the
    order list does not cover anything (it has no children), and does not
    turn into available stock either - open orders still never count as
    stock (unchanged rule from the original availability logic).

    Adds four columns to a copy of bom_df:
      - Is_Ordered: this row's own material number found in the order list.
      - Covered_By: material number of the nearest ordered-assembly
        ancestor, or "" if this row is not covered by one.
      - Order_KANBAN / Order_Delivery_Date: context looked up directly on
        this row's own material number (blank if Is_Ordered is False).

    Requires bom_df to be in original BOM (Row_Order) sequence, since the
    nesting is reconstructed from the Level column via a simple ancestor
    stack - the same "next row with Level <= current level ends the
    subtree" boundary rule already used by build_bom_view.
    """
    out = bom_df.sort_values("Row_Order").reset_index(drop=True)

    is_ordered, covered_by, kanban, delivery_date = [], [], [], []

    if order_df is not None and not order_df.empty:
        by_number, by_zeugnis = _lookup_tables(order_df)
    else:
        by_number = by_zeugnis = None

    # Stack of (level, covering_material_number_or_None) for the current
    # chain of ancestors, shallowest first.
    stack = []
    for _, row in out.iterrows():
        level = row["Level"]
        while stack and stack[-1][0] >= level:
            stack.pop()
        inherited_cover = stack[-1][1] if stack else None
        covered_by.append(inherited_cover or "")

        match = None
        if by_number is not None:
            zeugnis_nr = material_keys(row["Material_Number"])["zeugnis_nr"]
            match = _match_order_row(by_number, by_zeugnis, row["Material_Number"], zeugnis_nr)
        row_is_ordered = match is not None
        is_ordered.append(row_is_ordered)
        kanban.append(match["KANBAN"] if match is not None else "")
        delivery_date.append(match["Delivery_Date"] if match is not None else "")

        if row["Item_Type"] == "Assembly" and row_is_ordered:
            effective_cover = inherited_cover or row["Material_Number"]
            stack.append((level, effective_cover))
        else:
            stack.append((level, inherited_cover))

    out["Is_Ordered"] = is_ordered
    out["Covered_By"] = covered_by
    out["Order_KANBAN"] = kanban
    out["Order_Delivery_Date"] = delivery_date
    return out


def enrich_gaps_with_order_list(availability_df, order_df):
    """Attaches order context ONLY where Gap > 0 (the order list does not count as stock)."""
    out = availability_df.copy()
    for col in ["Order_Supplier", "Order_Qty", "Order_Delivery_Date", "Order_Delivery_Status", "Order_Number"]:
        out[col] = pd.Series([""] * len(out), dtype="object")

    if order_df is None or order_df.empty:
        return out

    by_number, by_zeugnis = _lookup_tables(order_df)

    for idx, row in out.iterrows():
        if row["Gap"] <= 0:
            continue
        match = _match_order_row(by_number, by_zeugnis, row["Material_Number"], row["Zeugnis_Nr"])
        if match is not None:
            out.at[idx, "Order_Supplier"] = match["Supplier"]
            out.at[idx, "Order_Qty"] = "" if pd.isna(match["Order_Qty"]) else str(match["Order_Qty"])
            out.at[idx, "Order_Delivery_Date"] = match["Delivery_Date"]
            out.at[idx, "Order_Delivery_Status"] = match["Delivery_Status"]
            out.at[idx, "Order_Number"] = match["Order_Number"]
    return out


def attach_kanban(availability_df, order_df):
    """Attaches the KANBAN number to ALL materials (not just gaps) - pure reference info."""
    out = availability_df.copy()
    out["KANBAN"] = pd.Series([""] * len(out), dtype="object")

    if order_df is None or order_df.empty:
        return out

    by_number, by_zeugnis = _lookup_tables(order_df)

    for idx, row in out.iterrows():
        match = _match_order_row(by_number, by_zeugnis, row["Material_Number"], row["Zeugnis_Nr"])
        if match is not None:
            out.at[idx, "KANBAN"] = match["KANBAN"]
    return out


def extract_decms_stock(order_df, demand_df):
    """Additional storage source "main warehouse (DECMS)" from the order list.

    Rule (confirmed by the business): if "demand covered" (column AG) == "OK"
    AND "stock avai. DECMS..." (column AD) carries a stock value, that value
    counts as additional available stock, source "main warehouse (DECMS)".

    Special case 9999: per the business this means "hardware, practically
    always enough available". To avoid showing a misleading raw number
    (9999) in the reports next to real piece counts, the quantity is instead
    capped exactly at that material's demand (result: Gap=0/OK, without
    complicating the generic availability-summation logic with a special
    case). Every main-warehouse entry - whether it came from the 9999
    sentinel or a real stock figure - is labelled identically, since from
    the report reader's point of view both simply mean "available from the
    main warehouse".
    """
    empty = pd.DataFrame(columns=["Material_Key", "Quantity", "Location", "Source"])
    if order_df is None or order_df.empty:
        return empty

    demand_by_material = demand_df.set_index("Material_Number")["Total_Demand"].to_dict()
    candidates = order_df.drop_duplicates("Material_Number")

    rows = []
    for _, r in candidates.iterrows():
        if str(r.get("Demand_Covered", "")).strip().upper() != "OK":
            continue
        raw_qty = r.get("DECMS_Stock")
        if raw_qty is None or pd.isna(raw_qty) or raw_qty <= 0:
            continue

        material = r["Material_Number"]
        if raw_qty == DECMS_HARDWARE_SENTINEL:
            demand = demand_by_material.get(material)
            if not demand or demand <= 0:
                continue  # no demand for this material - special case irrelevant
            qty = demand
        else:
            qty = raw_qty

        # No separate location for main-warehouse stock - the source label
        # already says where it is; keeping Location empty avoids repeating
        # the same information in both the Locations and Sources columns.
        rows.append({
            "Material_Key": material, "Quantity": qty,
            "Location": "", "Source": DECMS_SOURCE_LABEL,
        })

    return pd.DataFrame(rows, columns=["Material_Key", "Quantity", "Location", "Source"]) if rows else empty


ORDERED_EXTERNAL = "ORDERED (EXTERNAL)"

# Statuses that do not block an assembly's internal build readiness: OK
# (in stock) and COVERED (zero net demand because a purchased-complete
# ancestor assembly already includes it) both mean "nothing to do here".
_READINESS_SATISFIED_STATUSES = {"OK", "COVERED"}


def categorize_location(sources_str, kanban, sap_label, decms_label=DECMS_SOURCE_LABEL):
    """Maps the raw Sources string onto the three named physical locations.

    KANBAN materials take priority - regardless of where any matched stock
    happens to sit, a KANBAN-managed material is replenished via the Lift.
    """
    if kanban:
        return "Lift"
    if not sources_str:
        return ""
    labels = []
    for part in str(sources_str).split(" | "):
        part = part.strip()
        if part == sap_label:
            labels.append("Dummy Warehouse")
        elif part == decms_label:
            labels.append("Main Warehouse")
        elif part:
            labels.append(part)
    return _unique_join(labels)


def build_bom_view(bom_df, availability_df, stock_df=None, sap_label="SAP Stock"):
    """Full BOM view with per-row part status + bottom-up assembly readiness.

    If bom_df carries the order-context columns from
    enrich_bom_with_order_context (Is_Ordered / Order_KANBAN /
    Order_Delivery_Date), an assembly that is itself ordered as a complete
    unit is reported as ORDERED_EXTERNAL and its bottom-up readiness rollup
    is skipped entirely - its own procurement status is what matters, not
    the status of components that arrive pre-assembled inside it. Absent
    those columns (e.g. hand-built bom_df in tests), behaviour is
    unchanged from before.

    If stock_df is given, assemblies also get their OWN Available_Qty /
    Locations / Sources looked up directly (some assemblies are kept as
    spares or already-built units) - purely informational, this has no
    effect on demand or on the readiness rollup.
    """
    has_order_context = "Is_Ordered" in bom_df.columns
    status_by_material = availability_df.set_index("Material_Number")
    stock_lookup = _stock_lookup(stock_df) if stock_df is not None else {}

    view = bom_df.copy().reset_index(drop=True)
    for col in ["Total_Demand", "Available_Qty", "Gap", "Locations", "Sources", "Status", "Usage_Count"]:
        view[col] = None
    view["Assembly_Readiness"] = ""
    view["Ordered"] = view["Is_Ordered"].map({True: "Yes", False: "No"}) if has_order_context else ""
    view["Delivery_Date"] = view["Order_Delivery_Date"] if has_order_context else ""
    view["Comment"] = ""

    for idx, row in view.iterrows():
        if row["Item_Type"] == "Part":
            mat = row["Material_Number"]
            if mat in status_by_material.index:
                s = status_by_material.loc[mat]
                view.at[idx, "Total_Demand"] = s["Total_Demand"]
                view.at[idx, "Available_Qty"] = s["Available_Qty"]
                view.at[idx, "Gap"] = s["Gap"]
                view.at[idx, "Locations"] = s["Locations"]
                view.at[idx, "Sources"] = s["Sources"]
                view.at[idx, "Status"] = s["Status"]
                view.at[idx, "Usage_Count"] = s["Usage_Count"]
        elif row["Item_Type"] == "Assembly" and stock_lookup:
            # Own-stock check: some assemblies are themselves stocked
            # (spares, pre-built units) - independent of demand/coverage.
            keys = {row["Material_Number"], material_keys(row["Material_Number"])["zeugnis_nr"]}
            qty_sum, locs, srcs = 0, [], []
            for k in keys:
                if k in stock_lookup:
                    qty_sum += stock_lookup[k]["Quantity"]
                    if stock_lookup[k]["Location"]:
                        locs.append(stock_lookup[k]["Location"])
                    if stock_lookup[k]["Source"]:
                        srcs.append(stock_lookup[k]["Source"])
            view.at[idx, "Available_Qty"] = qty_sum
            view.at[idx, "Locations"] = _unique_join(locs)
            view.at[idx, "Sources"] = _unique_join(srcs)

        kanban = row["Order_KANBAN"] if has_order_context else ""
        view.at[idx, "Location"] = categorize_location(view.at[idx, "Sources"], kanban, sap_label)

    for i in range(len(view)):
        if view.loc[i, "Item_Type"] != "Assembly":
            continue

        if has_order_context and bool(view.loc[i, "Is_Ordered"]):
            # Priority rule: an assembly ordered as a complete unit is
            # evaluated on its OWN procurement status, never on whether its
            # (already-included) components happen to look "missing".
            view.at[i, "Assembly_Readiness"] = ORDERED_EXTERNAL
            continue

        current_level = view.loc[i, "Level"]
        end_idx = len(view)
        for j in range(i + 1, len(view)):
            if view.loc[j, "Level"] <= current_level:
                end_idx = j
                break
        subtree_parts = view.iloc[i + 1:end_idx]
        subtree_parts = subtree_parts[subtree_parts["Item_Type"] == "Part"]
        if subtree_parts.empty:
            readiness = "N/A"
        else:
            statuses = subtree_parts["Status"].tolist()
            if all(s in _READINESS_SATISFIED_STATUSES for s in statuses):
                readiness = "READY TO BUILD"
            elif any(s == "MISSING" for s in statuses):
                readiness = "BLOCKED"
            else:
                readiness = "PARTIAL BUILD"
        view.at[i, "Assembly_Readiness"] = readiness

    return view
