from src.material_keys import (
    derive_item_type_from_code,
    derive_zeugnis_nr,
    material_keys,
    normalize_material_number,
)


class TestNormalizeMaterialNumber:
    def test_plain_15_char_number_is_unchanged(self):
        assert normalize_material_number("3XAA113261A0001") == "3XAA113261A0001"

    def test_strips_parenthesis_suffix(self):
        # Fatal bug report case 1.
        assert normalize_material_number("3XAA099204P6003(02)") == "3XAA099204P6003"

    def test_strips_underscore_number_suffix(self):
        # Fatal bug report case 2.
        assert normalize_material_number("GCE0920435P0100_002") == "GCE0920435P0100"

    def test_strips_pos_suffix(self):
        assert normalize_material_number("3XAA086501A0001_POS003") == "3XAA086501A0001"

    def test_strips_file_extension_suffix(self):
        assert normalize_material_number("3XAA086849P0001.SLDPRT") == "3XAA086849P0001"

    def test_short_10_char_number_is_unchanged(self):
        # Known exception: some numbers only have 10 characters.
        assert normalize_material_number("3XAA101456") == "3XAA101456"

    def test_lowercase_input_is_uppercased(self):
        assert normalize_material_number("gce0920435p0100") == "GCE0920435P0100"

    def test_whitespace_is_stripped(self):
        assert normalize_material_number("  3XAA113261A0001  ") == "3XAA113261A0001"

    def test_none_returns_empty_string(self):
        assert normalize_material_number(None) == ""

    def test_nan_like_string_returns_empty(self):
        assert normalize_material_number("nan") == ""

    def test_empty_string_returns_empty(self):
        assert normalize_material_number("") == ""


class TestDeriveZeugnisNr:
    def test_last_10_characters_of_15_char_number(self):
        assert derive_zeugnis_nr("3XAA113261A0001") == "13261A0001"

    def test_short_number_returned_unchanged(self):
        assert derive_zeugnis_nr("3XAA101456") == "3XAA101456"

    def test_empty_input_returns_empty(self):
        assert derive_zeugnis_nr("") == ""

    def test_derived_after_truncation_matches_real_sap_example(self):
        # Regression for the fatal bug: truncate first, then derive.
        cleaned = normalize_material_number("GCE0920435P0100_002")
        assert derive_zeugnis_nr(cleaned) == "20435P0100"


class TestDeriveItemTypeFromCode:
    def test_part_marker(self):
        assert derive_item_type_from_code("3XAA086849P0001") == "Part"

    def test_assembly_marker(self):
        assert derive_item_type_from_code("3XAA113261A0001") == "Assembly"

    def test_unknown_marker_returns_none(self):
        assert derive_item_type_from_code("3XAA1234560001") is None

    def test_too_short_returns_none(self):
        assert derive_item_type_from_code("ABCD") is None

    def test_empty_returns_none(self):
        assert derive_item_type_from_code("") is None


class TestMaterialKeys:
    def test_returns_both_keys(self):
        keys = material_keys("GCE0920435P0100_002")
        assert keys == {"number": "GCE0920435P0100", "zeugnis_nr": "20435P0100"}
