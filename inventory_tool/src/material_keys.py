"""Material key normalization.

Validated against real Windchill/SAP sample data:
- The material number is always the first 15 characters of the raw value.
  Anything after that is a suffix (revision/position marker like "(02)",
  "_002", "_POS003", file extensions like ".SLDPRT") and is discarded -
  regardless of the pattern. Example: "GCE0920435P0100_002" ->
  "GCE0920435P0100".
- The SAP certificate number ("Zeugnis-Nr") is the last 10 characters of
  the (cleaned, 15-character) material number, e.g. "3XAA113261A0001" ->
  "13261A0001".
- Exception: some material numbers are already only 10 characters long
  (data entry error in SAP); in that case the number is used unchanged as
  the certificate number, since there is nothing left to cut.
- The Part/Assembly marker is the 5th character from the right ("P" or "A").

Deliberately NO fuzzy/substring matching (unlike the legacy script): that
carries a real risk of false matches in inventory data. The 15-character
rule is a hard positional rule, not a guess.
"""

MATERIAL_NUMBER_LENGTH = 15


def normalize_material_number(raw) -> str:
    """Cleans a raw material number/ID down to the canonical key.

    Always takes only the first 15 characters - shorter numbers (the known
    10-character exception) are left unchanged.
    """
    if raw is None:
        return ""
    s = str(raw).strip().upper()
    if not s or s == "NAN":
        return ""
    return s[:MATERIAL_NUMBER_LENGTH]


def derive_zeugnis_nr(material_number: str) -> str:
    """Last 10 characters, or the number itself if it already has <=10 chars."""
    if not material_number:
        return ""
    if len(material_number) <= 10:
        return material_number
    return material_number[-10:]


def derive_item_type_from_code(material_number: str):
    """Reads the Part/Assembly marker at position 5 from the right.

    Purely a cross-check against the BOM hierarchy - not the sole source of
    truth, since not every number follows the pattern. Returns "Part",
    "Assembly", or None (undetermined).
    """
    if not material_number or len(material_number) < 5:
        return None
    code_char = material_number[-5]
    if code_char == "P":
        return "Part"
    if code_char == "A":
        return "Assembly"
    return None


def material_keys(raw_number) -> dict:
    """Returns the two keys a material is matched on."""
    number = normalize_material_number(raw_number)
    zeugnis_nr = derive_zeugnis_nr(number)
    return {"number": number, "zeugnis_nr": zeugnis_nr}
