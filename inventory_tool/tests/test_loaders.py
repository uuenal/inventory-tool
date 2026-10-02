import os

import pytest
import yaml

from src import loaders

CONFIG_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config", "mapping.yaml")


@pytest.fixture
def mapping():
    with open(CONFIG_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f)


BOM_HEADER = ["Level", "Number", "Title Local Lang.", "Quantity", "Critical Part"]
BOM_DATA_ROWS = [
    [0, "3XAA113261A0001", "Top Assembly", 1, "No"],
    [1, "3XAA086849P0001", "Tube", 3, "No"],
    [1, "3XAA095923P0001", "Flange", 6, "Yes"],
]


class TestLoadBom:
    def test_loads_bom_with_header_in_first_row(self, xlsx_path, mapping):
        path = xlsx_path("Default", [BOM_HEADER] + BOM_DATA_ROWS)
        bom_df = loaders.load_bom(path, mapping)
        assert len(bom_df) == 3
        assert bom_df.iloc[0]["Item_Type"] == "Assembly"
        assert bom_df.iloc[1]["Item_Type"] == "Part"

    def test_finds_header_row_after_preamble_rows(self, xlsx_path, mapping):
        # Real-world regression: some Windchill exports have a title/blank
        # row before the actual header.
        rows = [["Windchill export - Project X"], [None], BOM_HEADER] + BOM_DATA_ROWS
        path = xlsx_path("Default", rows)
        bom_df = loaders.load_bom(path, mapping)
        assert len(bom_df) == 3
        assert bom_df.iloc[0]["Material_Number"] == "3XAA113261A0001"

    def test_raises_clear_error_when_columns_missing(self, xlsx_path, mapping):
        path = xlsx_path("Default", [["Foo", "Bar"], [1, 2]])
        with pytest.raises(ValueError, match="header row"):
            loaders.load_bom(path, mapping)

    def test_truncates_material_number_suffix(self, xlsx_path, mapping):
        rows = [BOM_HEADER, [1, "GCE0920435P0100_002", "Screw", 1, "No"]]
        path = xlsx_path("Default", rows)
        bom_df = loaders.load_bom(path, mapping)
        assert bom_df.iloc[0]["Material_Number"] == "GCE0920435P0100"


SAP_HEADER = ["Zeugnis-Nr", "Gesamtbestand", "Lagerplatz"]
SAP_DATA_ROWS = [
    ["96707P0001", 2, "1-04-08/1"],
    ["95043P0001", 25, "1-16-05/1"],
]


class TestLoadMappedStorageSource:
    def test_loads_sap_stock(self, xlsx_path, mapping):
        path = xlsx_path("Sheet1", [SAP_HEADER] + SAP_DATA_ROWS)
        sap_cfg = next(s for s in mapping["storage_sources"] if s["id"] == "sap")
        sap_df = loaders.load_mapped_storage_source(path, sap_cfg)
        assert len(sap_df) == 2
        assert set(sap_df.columns) == {"Material_Key", "Quantity", "Location", "Source"}

    def test_finds_header_after_preamble_rows(self, xlsx_path, mapping):
        rows = [["SAP export - Plant 1"], [None], SAP_HEADER] + SAP_DATA_ROWS
        path = xlsx_path("Sheet1", rows)
        sap_cfg = next(s for s in mapping["storage_sources"] if s["id"] == "sap")
        sap_df = loaders.load_mapped_storage_source(path, sap_cfg)
        assert len(sap_df) == 2

    def test_raises_clear_error_when_columns_missing(self, xlsx_path, mapping):
        path = xlsx_path("Sheet1", [["Foo", "Bar"], [1, 2]])
        sap_cfg = next(s for s in mapping["storage_sources"] if s["id"] == "sap")
        with pytest.raises(ValueError, match="header row"):
            loaders.load_mapped_storage_source(path, sap_cfg)


class TestLoadGenericStorageSource:
    def test_detects_known_column_name_variants(self, xlsx_path):
        rows = [["Material Number", "Stock", "Storage"], ["ABC0000000P0001", 5, "Rack 1"]]
        path = xlsx_path("Sheet1", rows)
        df, warns = loaders.load_generic_storage_source(path, "Storage 2")
        assert not warns
        assert len(df) == 1
        assert df.iloc[0]["Quantity"] == 5

    def test_warns_and_returns_empty_when_unrecognized(self, xlsx_path):
        rows = [["Foo", "Bar"], [1, 2]]
        path = xlsx_path("Sheet1", rows)
        df, warns = loaders.load_generic_storage_source(path, "Storage 2")
        assert df.empty
        assert len(warns) == 1
        assert "Storage 2" in str(warns[0])


def make_order_list_rows():
    header = [
        None, None, "Part ID", "rev.", "Part description", None, None, None, None,
        "Phase 1 (test)", None, "SAP ID", "KANBAN", None, None, "demand covered", None,
        "stock avai. DECMS xx.xx.2026", "Supplier", None, "Order qty.", None, "delivery status",
        "Order no.", "Delivery date",
    ]
    data_row = [
        None, None, "PART000000P0001", "A.1", "Test Part", None, None, None, None,
        3, None, "SAP-1", "K-1", None, None, "OK", None, 76, "ACME Corp", None, 5, None,
        "available", "PO-1", "2026-01-01",
    ]
    return [header, data_row]


class TestLoadOrderList:
    def test_extracts_core_and_new_columns(self, xlsx_path, mapping):
        path = xlsx_path("ORDER LIST (A.1)", make_order_list_rows())
        order_df = loaders.load_order_list(path, mapping)
        assert len(order_df) == 1
        row = order_df.iloc[0]
        assert row["Material_Number"] == "PART000000P0001"
        assert row["KANBAN"] == "K-1"
        assert row["Supplier"] == "ACME Corp"
        assert row["Order_Number"] == "PO-1"
        assert row["Demand_Covered"] == "OK"
        assert row["DECMS_Stock"] == 76

    def test_raises_when_no_matching_sheet_found(self, xlsx_path, mapping):
        path = xlsx_path("Unrelated Sheet", [["a", "b"]])
        with pytest.raises(ValueError, match="ORDER LIST"):
            loaders.load_order_list(path, mapping)
