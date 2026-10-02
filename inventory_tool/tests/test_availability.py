import pandas as pd

from src import availability


def make_bom_row(level, item_type, material_number, quantity_per_unit=1,
                  title="", critical="No", row_order=0):
    return {
        "Level": level, "Item_Type": item_type, "Material_Number": material_number,
        "Quantity_Per_Unit": quantity_per_unit, "Title": title, "Critical": critical,
        "Row_Order": row_order,
    }


class TestAggregateDemand:
    def test_sums_demand_across_multiple_bom_occurrences(self):
        # Core bugfix under test: the same material used under three
        # different assemblies must have its demand SUMMED, not
        # independently checked three times against the same stock.
        bom_df = pd.DataFrame([
            make_bom_row(0, "Assembly", "ASM0000000A0001", row_order=0),
            make_bom_row(1, "Part", "PART000000P0001", quantity_per_unit=3, row_order=1),
            make_bom_row(0, "Assembly", "ASM0000000A0002", row_order=2),
            make_bom_row(1, "Part", "PART000000P0001", quantity_per_unit=12, row_order=3),
            make_bom_row(0, "Assembly", "ASM0000000A0003", row_order=4),
            make_bom_row(1, "Part", "PART000000P0001", quantity_per_unit=2, row_order=5),
        ])
        demand = availability.aggregate_demand(bom_df, num_units=1)
        row = demand[demand.Material_Number == "PART000000P0001"].iloc[0]
        assert row["Usage_Count"] == 3
        assert row["Quantity_Per_Unit_Sum"] == 17
        assert row["Total_Demand"] == 17

    def test_multiplies_by_num_units(self):
        bom_df = pd.DataFrame([make_bom_row(0, "Part", "PART000000P0001", quantity_per_unit=5)])
        demand = availability.aggregate_demand(bom_df, num_units=4)
        assert demand.iloc[0]["Total_Demand"] == 20

    def test_assemblies_are_excluded_from_demand(self):
        bom_df = pd.DataFrame([
            make_bom_row(0, "Assembly", "ASM0000000A0001"),
            make_bom_row(1, "Part", "PART000000P0001", quantity_per_unit=1),
        ])
        demand = availability.aggregate_demand(bom_df, num_units=1)
        assert "ASM0000000A0001" not in demand.Material_Number.values

    def test_critical_yes_if_any_occurrence_is_critical(self):
        bom_df = pd.DataFrame([
            make_bom_row(0, "Part", "PART000000P0001", critical="No"),
            make_bom_row(0, "Part", "PART000000P0001", critical="Yes"),
        ])
        demand = availability.aggregate_demand(bom_df, num_units=1)
        assert demand.iloc[0]["Critical"] == "Yes"


def make_demand_row(material_number, total_demand, zeugnis_nr=None, critical="No"):
    return {
        "Material_Number": material_number,
        "Zeugnis_Nr": zeugnis_nr or material_number[-10:],
        "Total_Demand": total_demand, "Title": "", "Critical": critical,
        "Usage_Count": 1, "Quantity_Per_Unit_Sum": total_demand,
    }


def make_stock_row(material_key, quantity, location="", source="Test"):
    return {"Material_Key": material_key, "Quantity": quantity, "Location": location, "Source": source}


class TestComputeAvailability:
    def test_status_ok_when_fully_covered(self):
        demand_df = pd.DataFrame([make_demand_row("PART000000P0001", 10)])
        stock_df = pd.DataFrame([make_stock_row("PART000000P0001", 10)])
        result = availability.compute_availability(demand_df, stock_df)
        assert result.iloc[0]["Status"] == "OK"
        assert result.iloc[0]["Gap"] == 0

    def test_status_partial_when_some_stock(self):
        demand_df = pd.DataFrame([make_demand_row("PART000000P0001", 10)])
        stock_df = pd.DataFrame([make_stock_row("PART000000P0001", 4)])
        result = availability.compute_availability(demand_df, stock_df)
        assert result.iloc[0]["Status"] == "PARTIAL"
        assert result.iloc[0]["Gap"] == 6

    def test_status_missing_when_no_stock(self):
        demand_df = pd.DataFrame([make_demand_row("PART000000P0001", 10)])
        stock_df = pd.DataFrame(columns=["Material_Key", "Quantity", "Location", "Source"])
        result = availability.compute_availability(demand_df, stock_df)
        assert result.iloc[0]["Status"] == "MISSING"
        assert result.iloc[0]["Gap"] == 10

    def test_matches_by_zeugnis_nr_when_stock_uses_short_key(self):
        demand_df = pd.DataFrame([make_demand_row("3XAA113261A0001", 5, zeugnis_nr="13261A0001")])
        stock_df = pd.DataFrame([make_stock_row("13261A0001", 5)])
        result = availability.compute_availability(demand_df, stock_df)
        assert result.iloc[0]["Status"] == "OK"

    def test_sums_stock_across_multiple_sources(self):
        demand_df = pd.DataFrame([make_demand_row("PART000000P0001", 10)])
        stock_df = pd.DataFrame([
            make_stock_row("PART000000P0001", 4, location="Rack A", source="SAP Stock"),
            make_stock_row("0000000P0001", 6, location="Rack B", source="Storage 2"),
        ])
        # Second row keyed by Zeugnis-Nr (last 10 chars) to simulate a
        # different source using the short key.
        stock_df.loc[1, "Material_Key"] = "PART000000P0001"[-10:]
        result = availability.compute_availability(demand_df, stock_df)
        assert result.iloc[0]["Available_Qty"] == 10
        assert result.iloc[0]["Status"] == "OK"
        assert "Rack A" in result.iloc[0]["Locations"]
        assert "Rack B" in result.iloc[0]["Locations"]

    def test_does_not_double_count_when_both_keys_identical(self):
        # Edge case: material number already <=10 chars, so Material_Number
        # == Zeugnis_Nr. Must not count the same stock row twice.
        demand_df = pd.DataFrame([make_demand_row("SHORT00001", 5, zeugnis_nr="SHORT00001")])
        stock_df = pd.DataFrame([make_stock_row("SHORT00001", 5)])
        result = availability.compute_availability(demand_df, stock_df)
        assert result.iloc[0]["Available_Qty"] == 5


class TestExtractDecmsStock:
    def test_normal_value_is_used_as_is(self):
        order_df = pd.DataFrame([{
            "Material_Number": "PART000000P0001", "Zeugnis_Nr": "0000P0001",
            "Demand_Covered": "OK", "DECMS_Stock": 76.0,
        }])
        demand_df = pd.DataFrame([make_demand_row("PART000000P0001", 10)])
        result = availability.extract_decms_stock(order_df, demand_df)
        assert len(result) == 1
        assert result.iloc[0]["Quantity"] == 76.0
        assert result.iloc[0]["Source"] == availability.DECMS_SOURCE_LABEL
        assert result.iloc[0]["Location"] == ""

    def test_sentinel_9999_is_capped_at_material_demand(self):
        order_df = pd.DataFrame([{
            "Material_Number": "PART000000P0001", "Zeugnis_Nr": "0000P0001",
            "Demand_Covered": "OK", "DECMS_Stock": 9999.0,
        }])
        demand_df = pd.DataFrame([make_demand_row("PART000000P0001", 6)])
        result = availability.extract_decms_stock(order_df, demand_df)
        assert result.iloc[0]["Quantity"] == 6
        assert result.iloc[0]["Source"] == availability.DECMS_SOURCE_LABEL

    def test_sentinel_9999_skipped_when_material_has_no_demand(self):
        order_df = pd.DataFrame([{
            "Material_Number": "PART000000P0001", "Zeugnis_Nr": "0000P0001",
            "Demand_Covered": "OK", "DECMS_Stock": 9999.0,
        }])
        demand_df = pd.DataFrame([make_demand_row("OTHER00000P0002", 6)])
        result = availability.extract_decms_stock(order_df, demand_df)
        assert result.empty

    def test_ignored_when_demand_covered_is_not_ok(self):
        order_df = pd.DataFrame([{
            "Material_Number": "PART000000P0001", "Zeugnis_Nr": "0000P0001",
            "Demand_Covered": "NIO", "DECMS_Stock": 50.0,
        }])
        demand_df = pd.DataFrame([make_demand_row("PART000000P0001", 10)])
        result = availability.extract_decms_stock(order_df, demand_df)
        assert result.empty

    def test_ignored_when_stock_value_is_zero_or_blank(self):
        order_df = pd.DataFrame([
            {"Material_Number": "PART000000P0001", "Zeugnis_Nr": "0000P0001",
             "Demand_Covered": "OK", "DECMS_Stock": 0.0},
            {"Material_Number": "PART000000P0002", "Zeugnis_Nr": "0000P0002",
             "Demand_Covered": "OK", "DECMS_Stock": None},
        ])
        demand_df = pd.DataFrame([
            make_demand_row("PART000000P0001", 10),
            make_demand_row("PART000000P0002", 10),
        ])
        result = availability.extract_decms_stock(order_df, demand_df)
        assert result.empty

    def test_empty_order_list_returns_empty_frame(self):
        demand_df = pd.DataFrame([make_demand_row("PART000000P0001", 10)])
        result = availability.extract_decms_stock(None, demand_df)
        assert result.empty


class TestEnrichGapsWithOrderList:
    def test_only_attaches_context_when_gap_positive(self):
        availability_df = pd.DataFrame([
            {"Material_Number": "PART000000P0001", "Zeugnis_Nr": "0000P0001", "Gap": 5},
            {"Material_Number": "PART000000P0002", "Zeugnis_Nr": "0000P0002", "Gap": 0},
        ])
        order_df = pd.DataFrame([
            {"Material_Number": "PART000000P0001", "Zeugnis_Nr": "0000P0001",
             "Supplier": "ACME", "Order_Qty": 5, "Delivery_Date": "2026-01-01",
             "Delivery_Status": "ordered", "Order_Number": "PO123"},
            {"Material_Number": "PART000000P0002", "Zeugnis_Nr": "0000P0002",
             "Supplier": "ACME", "Order_Qty": 5, "Delivery_Date": "2026-01-01",
             "Delivery_Status": "ordered", "Order_Number": "PO124"},
        ])
        result = availability.enrich_gaps_with_order_list(availability_df, order_df)
        assert result.iloc[0]["Order_Number"] == "PO123"
        assert result.iloc[1]["Order_Number"] == ""


class TestAttachKanban:
    def test_attaches_kanban_regardless_of_gap(self):
        availability_df = pd.DataFrame([
            {"Material_Number": "PART000000P0001", "Zeugnis_Nr": "0000P0001", "Gap": 0},
        ])
        order_df = pd.DataFrame([
            {"Material_Number": "PART000000P0001", "Zeugnis_Nr": "0000P0001", "KANBAN": "K-99"},
        ])
        result = availability.attach_kanban(availability_df, order_df)
        assert result.iloc[0]["KANBAN"] == "K-99"


class TestBuildBomView:
    def test_assembly_ready_when_all_parts_ok(self):
        bom_df = pd.DataFrame([
            make_bom_row(0, "Assembly", "ASM0000000A0001", row_order=0),
            make_bom_row(1, "Part", "PART000000P0001", row_order=1),
            make_bom_row(1, "Part", "PART000000P0002", row_order=2),
        ])
        availability_df = pd.DataFrame([
            {**make_demand_row("PART000000P0001", 1), "Available_Qty": 1, "Gap": 0,
             "Locations": "", "Sources": "", "Status": "OK"},
            {**make_demand_row("PART000000P0002", 1), "Available_Qty": 1, "Gap": 0,
             "Locations": "", "Sources": "", "Status": "OK"},
        ])
        view = availability.build_bom_view(bom_df, availability_df)
        assert view.iloc[0]["Assembly_Readiness"] == "READY TO BUILD"

    def test_assembly_blocked_when_any_part_missing(self):
        bom_df = pd.DataFrame([
            make_bom_row(0, "Assembly", "ASM0000000A0001", row_order=0),
            make_bom_row(1, "Part", "PART000000P0001", row_order=1),
            make_bom_row(1, "Part", "PART000000P0002", row_order=2),
        ])
        availability_df = pd.DataFrame([
            {**make_demand_row("PART000000P0001", 1), "Available_Qty": 1, "Gap": 0,
             "Locations": "", "Sources": "", "Status": "OK"},
            {**make_demand_row("PART000000P0002", 1), "Available_Qty": 0, "Gap": 1,
             "Locations": "", "Sources": "", "Status": "MISSING"},
        ])
        view = availability.build_bom_view(bom_df, availability_df)
        assert view.iloc[0]["Assembly_Readiness"] == "BLOCKED"

    def test_assembly_partial_when_mixed_ok_and_partial(self):
        bom_df = pd.DataFrame([
            make_bom_row(0, "Assembly", "ASM0000000A0001", row_order=0),
            make_bom_row(1, "Part", "PART000000P0001", row_order=1),
            make_bom_row(1, "Part", "PART000000P0002", row_order=2),
        ])
        availability_df = pd.DataFrame([
            {**make_demand_row("PART000000P0001", 1), "Available_Qty": 1, "Gap": 0,
             "Locations": "", "Sources": "", "Status": "OK"},
            {**make_demand_row("PART000000P0002", 2), "Available_Qty": 1, "Gap": 1,
             "Locations": "", "Sources": "", "Status": "PARTIAL"},
        ])
        view = availability.build_bom_view(bom_df, availability_df)
        assert view.iloc[0]["Assembly_Readiness"] == "PARTIAL BUILD"

    def test_assembly_with_no_direct_or_nested_parts_is_na(self):
        bom_df = pd.DataFrame([make_bom_row(0, "Assembly", "ASM0000000A0001", row_order=0)])
        availability_df = pd.DataFrame(
            columns=["Material_Number", "Available_Qty", "Gap", "Locations", "Sources", "Status"]
        )
        view = availability.build_bom_view(bom_df, availability_df)
        assert view.iloc[0]["Assembly_Readiness"] == "N/A"


def make_order_row(material_number, kanban="", delivery_date=""):
    return {
        "Material_Number": material_number,
        "Zeugnis_Nr": material_number[-10:],
        "KANBAN": kanban,
        "Delivery_Date": delivery_date,
    }


def run_pipeline(bom_df, order_df, stock_df, num_units=1, sap_label="SAP Stock"):
    """End-to-end mini-pipeline mirroring main.py, for integration-style tests."""
    bom_df = availability.enrich_bom_with_order_context(bom_df, order_df)
    demand_df = availability.aggregate_demand(bom_df, num_units=num_units)
    avail_df = availability.compute_availability(demand_df, stock_df)
    bom_view = availability.build_bom_view(bom_df, avail_df, stock_df=stock_df, sap_label=sap_label)
    return avail_df, bom_view


class TestExternalAssemblyCoverage:
    """Covers the 'ordered as a complete unit' business rule end to end -
    the scenario at the heart of the change request."""

    def test_ordered_assembly_covers_its_children(self):
        bom_df = pd.DataFrame([
            make_bom_row(0, "Assembly", "A100000000A0001", row_order=0),
            make_bom_row(1, "Part", "P101000000P0001", quantity_per_unit=2, row_order=1),
            make_bom_row(1, "Part", "P102000000P0001", quantity_per_unit=3, row_order=2),
        ])
        order_df = pd.DataFrame([make_order_row("A100000000A0001")])
        stock_df = pd.DataFrame(columns=["Material_Key", "Quantity", "Location", "Source"])

        avail_df, bom_view = run_pipeline(bom_df, order_df, stock_df)

        # Neither child was ever physically in stock, but both must read as
        # COVERED, never MISSING - they arrive inside the purchased assembly.
        p101 = avail_df[avail_df.Material_Number == "P101000000P0001"].iloc[0]
        p102 = avail_df[avail_df.Material_Number == "P102000000P0001"].iloc[0]
        assert p101["Status"] == "COVERED"
        assert p101["Total_Demand"] == 0
        assert p102["Status"] == "COVERED"

        assembly_row = bom_view.iloc[0]
        assert assembly_row["Assembly_Readiness"] == availability.ORDERED_EXTERNAL
        assert assembly_row["Ordered"] == "Yes"

    def test_assembly_not_in_order_list_uses_existing_bottom_up_logic(self):
        bom_df = pd.DataFrame([
            make_bom_row(0, "Assembly", "A100000000A0001", row_order=0),
            make_bom_row(1, "Part", "P101000000P0001", quantity_per_unit=2, row_order=1),
            make_bom_row(1, "Part", "P102000000P0001", quantity_per_unit=3, row_order=2),
        ])
        order_df = pd.DataFrame(columns=["Material_Number", "Zeugnis_Nr", "KANBAN", "Delivery_Date"])
        stock_df = pd.DataFrame([
            {"Material_Key": "P101000000P0001", "Quantity": 2, "Location": "Rack 1", "Source": "SAP Stock"},
        ])

        avail_df, bom_view = run_pipeline(bom_df, order_df, stock_df)

        p101 = avail_df[avail_df.Material_Number == "P101000000P0001"].iloc[0]
        p102 = avail_df[avail_df.Material_Number == "P102000000P0001"].iloc[0]
        assert p101["Status"] == "OK"
        assert p102["Status"] == "MISSING"

        assembly_row = bom_view.iloc[0]
        assert assembly_row["Assembly_Readiness"] == "BLOCKED"
        assert assembly_row["Ordered"] == "No"

    def test_nested_assembly_only_inner_one_ordered(self):
        # A0 (not ordered)
        #  |- A1 (ordered) -> P1, P2 covered
        #  \- P3 (not covered, must be checked normally)
        # NOTE: the two assembly numbers are deliberately chosen so their
        # last 10 characters (the Zeugnis-Nr fallback match key) differ -
        # otherwise the Zeugnis-Nr fallback would make them match each
        # other's order-list entry, which is a data-collision concern of
        # the test fixture, not of the code under test.
        bom_df = pd.DataFrame([
            make_bom_row(0, "Assembly", "ZZZZZ00000A0000", row_order=0),
            make_bom_row(1, "Assembly", "ZZZZZ11111A0001", row_order=1),
            make_bom_row(2, "Part", "P001000000P0001", quantity_per_unit=1, row_order=2),
            make_bom_row(2, "Part", "P002000000P0001", quantity_per_unit=1, row_order=3),
            make_bom_row(1, "Part", "P003000000P0001", quantity_per_unit=1, row_order=4),
        ])
        order_df = pd.DataFrame([make_order_row("ZZZZZ11111A0001")])
        stock_df = pd.DataFrame([
            {"Material_Key": "P003000000P0001", "Quantity": 1, "Location": "Rack 1", "Source": "SAP Stock"},
        ])

        avail_df, bom_view = run_pipeline(bom_df, order_df, stock_df)

        p1 = avail_df[avail_df.Material_Number == "P001000000P0001"].iloc[0]
        p2 = avail_df[avail_df.Material_Number == "P002000000P0001"].iloc[0]
        p3 = avail_df[avail_df.Material_Number == "P003000000P0001"].iloc[0]
        assert p1["Status"] == "COVERED"
        assert p2["Status"] == "COVERED"
        assert p3["Status"] == "OK"

        a1_row = bom_view[bom_view.Material_Number == "ZZZZZ11111A0001"].iloc[0]
        assert a1_row["Assembly_Readiness"] == availability.ORDERED_EXTERNAL

        # A0 itself is NOT ordered -> normal bottom-up rollup over its whole
        # subtree (A1's children count as COVERED = satisfied, P3 is OK).
        a0_row = bom_view[bom_view.Material_Number == "ZZZZZ00000A0000"].iloc[0]
        assert a0_row["Ordered"] == "No"
        assert a0_row["Assembly_Readiness"] == "READY TO BUILD"

    def test_same_part_covered_in_one_branch_but_genuinely_needed_in_another(self):
        # P200 used once under ordered A1 (covered) and once under
        # non-ordered A2 (real, separate demand) - only the A2 occurrence
        # may count.
        bom_df = pd.DataFrame([
            make_bom_row(0, "Assembly", "A100000000A0001", quantity_per_unit=1, row_order=0),
            make_bom_row(1, "Part", "P200000000P0001", quantity_per_unit=5, row_order=1),
            make_bom_row(0, "Assembly", "A200000000A0002", quantity_per_unit=1, row_order=2),
            make_bom_row(1, "Part", "P200000000P0001", quantity_per_unit=3, row_order=3),
        ])
        order_df = pd.DataFrame([make_order_row("A100000000A0001")])
        stock_df = pd.DataFrame(columns=["Material_Key", "Quantity", "Location", "Source"])

        avail_df, _ = run_pipeline(bom_df, order_df, stock_df)

        p200 = avail_df[avail_df.Material_Number == "P200000000P0001"].iloc[0]
        assert p200["Total_Demand"] == 3
        assert p200["Status"] == "MISSING"

    def test_ordinary_ordered_part_does_not_cover_anything_and_still_needs_stock(self):
        # A plain Part being individually present in the order list must
        # NOT be treated as available stock (open orders are context only).
        bom_df = pd.DataFrame([
            make_bom_row(0, "Part", "P900000000P0001", quantity_per_unit=4, row_order=0),
        ])
        order_df = pd.DataFrame([make_order_row("P900000000P0001")])
        stock_df = pd.DataFrame(columns=["Material_Key", "Quantity", "Location", "Source"])

        avail_df, bom_view = run_pipeline(bom_df, order_df, stock_df)

        p900 = avail_df[avail_df.Material_Number == "P900000000P0001"].iloc[0]
        assert p900["Status"] == "MISSING"
        assert bom_view.iloc[0]["Ordered"] == "Yes"

    def test_kanban_location_takes_priority_over_stock_source(self):
        bom_df = pd.DataFrame([
            make_bom_row(0, "Part", "P300000000P0001", quantity_per_unit=1, row_order=0),
        ])
        order_df = pd.DataFrame([make_order_row("P300000000P0001", kanban="K-42")])
        stock_df = pd.DataFrame([
            {"Material_Key": "P300000000P0001", "Quantity": 5, "Location": "1-01-01", "Source": "SAP Stock"},
        ])

        _, bom_view = run_pipeline(bom_df, order_df, stock_df)
        assert bom_view.iloc[0]["Location"] == "Lift"

    def test_location_maps_sap_and_decms_labels(self):
        bom_df = pd.DataFrame([
            make_bom_row(0, "Part", "P400000000P0001", quantity_per_unit=1, row_order=0),
        ])
        order_df = pd.DataFrame(columns=["Material_Number", "Zeugnis_Nr", "KANBAN", "Delivery_Date"])
        stock_df = pd.DataFrame([
            {"Material_Key": "P400000000P0001", "Quantity": 2, "Location": "1-01-01", "Source": "SAP Stock"},
        ])

        _, bom_view = run_pipeline(bom_df, order_df, stock_df)
        assert bom_view.iloc[0]["Location"] == "Dummy Warehouse"


class TestCategorizeLocation:
    def test_kanban_wins_regardless_of_sources(self):
        assert availability.categorize_location("SAP Stock", "K-1", "SAP Stock") == "Lift"

    def test_maps_known_labels(self):
        result = availability.categorize_location(
            "SAP Stock | main warehouse (DECMS)", "", "SAP Stock"
        )
        assert result == "Dummy Warehouse | Main Warehouse"

    def test_unknown_source_passed_through(self):
        assert availability.categorize_location("Storage 2", "", "SAP Stock") == "Storage 2"

    def test_blank_when_nothing_matched(self):
        assert availability.categorize_location("", "", "SAP Stock") == ""
