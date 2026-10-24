"""Syllabus PDF extraction tests; all extracted rows remain draft suggestions."""
import io

import fitz
import pytest

from app.services.syllabus_extractor import SyllabusExtractionError, extract_syllabus_pdf


def make_pdf(lines):
    document = fitz.open()
    page = document.new_page()
    y = 72
    for line in lines:
        page.insert_text((72, y), line, fontsize=10)
        y += 18
    payload = document.tobytes()
    document.close()
    return payload


def test_extracts_subject_suggestions_with_roman_semester_and_labelled_credits():
    pdf = make_pdf([
        "SEMESTER I",
        "BSC101 Engineering Mathematics I Credits: 4",
        "BSC102 Engineering Physics (3 Credits)",
    ])
    result = extract_syllabus_pdf(pdf)
    assert result["page_count"] == 1
    assert result["candidate_count"] == 2
    assert result["parser_name"] == "acadpulse_syllabus_text_v1"
    by_code = {row["subject_code"]: row for row in result["candidates"]}
    assert by_code["BSC101"]["semester_number"] == 1
    assert by_code["BSC101"]["credits"] == "4"
    assert by_code["BSC102"]["credits"] == "3"
    assert all(row["assessment_structure"]["components"] == [] for row in result["candidates"])
    assert all(row["extraction_confidence"] < 1 for row in result["candidates"])


def test_skips_subject_suggestions_when_semester_cannot_be_inferred():
    result = extract_syllabus_pdf(make_pdf(["BSC101 Engineering Mathematics I Credits: 4"]))
    assert result["candidate_count"] == 0
    assert result["subjects_without_semester_skipped"] == 1


def test_duplicate_subject_candidates_are_deduplicated_within_semester():
    result = extract_syllabus_pdf(make_pdf([
        "SEMESTER III",
        "ESC301 Data Structures Credits: 3",
        "ESC301 Data Structures Credits: 3",
    ]))
    assert result["candidate_count"] == 1
    assert result["duplicate_candidates_skipped"] == 1
    assert result["candidates"][0]["semester_number"] == 3


def test_empty_and_non_pdf_files_are_rejected():
    with pytest.raises(SyllabusExtractionError):
        extract_syllabus_pdf(b"")
    with pytest.raises(SyllabusExtractionError, match="valid PDF"):
        extract_syllabus_pdf(b"not a PDF")


def test_scanned_like_pdf_is_reported_without_claiming_ocr_was_done():
    document = fitz.open()
    document.new_page()
    payload = document.tobytes()
    document.close()
    result = extract_syllabus_pdf(payload)
    assert result["is_likely_scanned"] is True
    assert result["pages_with_little_or_no_text"] == [1]
    assert result["candidate_count"] == 0


def test_page_with_multiple_semester_labels_is_not_assigned_by_last_heading():
    result = extract_syllabus_pdf(make_pdf([
        "SEMESTER I",
        "BSC101 Engineering Mathematics Credits: 4",
        "SEMESTER II",
        "BSC201 Engineering Physics Credits: 4",
    ]))
    assert result["candidate_count"] == 0
    assert result["ambiguous_semester_pages"] == [1]
