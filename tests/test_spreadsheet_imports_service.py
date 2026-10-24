"""Focused tests for the CSV/XLSX import service without importing Flask's app factory."""
from __future__ import annotations

import csv
import importlib.util
import io
import json
import re
import sys
import types
from pathlib import Path

import pytest
from openpyxl import Workbook

ROOT = Path(__file__).resolve().parents[1]
SERVICE_PATH = ROOT / "app" / "services" / "spreadsheet_imports.py"


def _minimal_secure_filename(value: str) -> str:
    # Used only when this build environment does not have Werkzeug installed.
    value = re.sub(r"[^A-Za-z0-9_.-]", "_", value)
    return value.strip("._")


@pytest.fixture(scope="module")
def service():
    """Load the service file directly so these tests also run before Flask install."""
    prior_utils = sys.modules.get("werkzeug.utils")
    prior_werkzeug = sys.modules.get("werkzeug")
    injected = False
    try:
        try:
            import werkzeug.utils  # noqa: F401
        except ImportError:
            injected = True
            werkzeug = types.ModuleType("werkzeug")
            utils = types.ModuleType("werkzeug.utils")
            utils.secure_filename = _minimal_secure_filename
            werkzeug.utils = utils
            sys.modules["werkzeug"] = werkzeug
            sys.modules["werkzeug.utils"] = utils
        spec = importlib.util.spec_from_file_location("spreadsheet_imports_test_module", SERVICE_PATH)
        module = importlib.util.module_from_spec(spec)
        assert spec and spec.loader
        spec.loader.exec_module(module)
        yield module
    finally:
        if injected:
            if prior_utils is None:
                sys.modules.pop("werkzeug.utils", None)
            else:
                sys.modules["werkzeug.utils"] = prior_utils
            if prior_werkzeug is None:
                sys.modules.pop("werkzeug", None)
            else:
                sys.modules["werkzeug"] = prior_werkzeug


def test_csv_headers_are_normalized_and_values_preserved(service):
    data = b"Roll No,Marks,Status,Reason\n07,18.5,present,\n08,,absent,\n"
    parsed = service.parse_spreadsheet(data, "class-marks.csv", workflow="coursework")
    assert len(parsed) == 2
    assert parsed[0]["values"] == {
        "roll_number": "07", "marks": "18.5", "status": "present", "correction_reason": ""
    }
    assert parsed[1]["row_number"] == 3
    assert parsed[1]["values"]["status"] == "absent"


def test_csv_requires_identity_and_workflow_columns(service):
    with pytest.raises(service.SpreadsheetImportError, match="identity column"):
        service.parse_spreadsheet(b"Name,Marks,Status\nA,10,present\n", "x.csv", workflow="coursework")
    with pytest.raises(service.SpreadsheetImportError, match="marks"):
        service.parse_spreadsheet(b"student_code,status\nS1,present\n", "x.csv", workflow="coursework")
    with pytest.raises(service.SpreadsheetImportError, match="duplicate"):
        service.parse_spreadsheet(b"roll no,roll_number,status,marks\n1,1,present,10\n", "x.csv", workflow="coursework")


def test_xlsx_first_sheet_is_read(service):
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["Student Code", "Attendance Status"])
    sheet.append(["AP-0001", "present"])
    sheet.append(["AP-0002", "excused"])
    buffer = io.BytesIO()
    workbook.save(buffer)
    parsed = service.parse_spreadsheet(buffer.getvalue(), "attendance.xlsx", workflow="attendance")
    assert len(parsed) == 2
    assert parsed[0]["values"] == {"student_code": "AP-0001", "status": "present"}


def test_preview_token_is_bound_to_user_workflow_and_target(service, tmp_path):
    token, payload = service.store_import_preview(
        upload_root=tmp_path, workflow="coursework", target_id=11, actor_id=7,
        filename="marks.csv", content=b"student_code,marks,status\nS1,12,present\n", rows=[{"valid": True}],
    )
    path, retrieved = service.read_import_preview(
        upload_root=tmp_path, token=token, workflow="coursework", target_id=11, actor_id=7,
    )
    assert retrieved["source_sha256"] == payload["source_sha256"]
    assert path.is_file()
    with pytest.raises(service.SpreadsheetImportError, match="another user or target"):
        service.read_import_preview(upload_root=tmp_path, token=token, workflow="attendance", target_id=11, actor_id=7)
    with pytest.raises(service.SpreadsheetImportError, match="another user or target"):
        service.read_import_preview(upload_root=tmp_path, token=token, workflow="coursework", target_id=11, actor_id=8)


def test_expired_preview_is_removed(service, tmp_path):
    token, payload = service.store_import_preview(
        upload_root=tmp_path, workflow="attendance", target_id=3, actor_id=2,
        filename="attendance.csv", content=b"student_code,status\nS1,present\n", rows=[{"valid": True}],
    )
    preview_path = tmp_path / "import_previews" / f"{token}.json"
    payload["expires_at"] = "2000-01-01T00:00:00+00:00"
    preview_path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(service.SpreadsheetImportError, match="expired after two hours"):
        service.read_import_preview(upload_root=tmp_path, token=token, workflow="attendance", target_id=3, actor_id=2)
    assert not preview_path.exists()
