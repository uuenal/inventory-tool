"""Shared test fixtures - small, self-contained (no dependency on any
real uploaded file), so the suite is reproducible anywhere."""

import openpyxl
import pytest


def write_sheet(path, sheet_name, rows):
    """Writes `rows` (list of lists) as the given sheet in a new workbook."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = sheet_name
    for row in rows:
        ws.append(row)
    wb.save(path)
    return path


@pytest.fixture
def xlsx_path(tmp_path):
    def _make(sheet_name, rows, filename="test.xlsx"):
        return write_sheet(tmp_path / filename, sheet_name, rows)
    return _make
