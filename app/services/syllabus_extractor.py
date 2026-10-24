"""Conservative syllabus PDF extraction for curriculum drafts.

This parser intentionally suggests candidate rows rather than creating official
curriculum directly. Every extracted subject starts in review_required state.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any


class SyllabusExtractionError(ValueError):
    """Raised when a PDF cannot safely be opened or profiled."""


_ROMAN_SEMESTERS = {
    "I": 1, "II": 2, "III": 3, "IV": 4, "V": 5, "VI": 6,
    "VII": 7, "VIII": 8, "IX": 9, "X": 10, "XI": 11, "XII": 12,
}
_SEMESTER_RE = re.compile(
    r"\b(?:SEMESTER|SEM\.?|TERM)\s*[-.:#]?\s*(XII|XI|IX|VIII|VII|VI|IV|V|III|II|I|1[0-2]|[1-9])\b",
    re.IGNORECASE,
)
_CODE_TITLE_RE = re.compile(
    r"^\s*(?P<code>(?=[A-Z0-9./_-]*[A-Z])(?=[A-Z0-9./_-]*\d)[A-Z0-9][A-Z0-9./_-]{1,39})[ \t]+(?P<rest>.+?)\s*$",
    re.IGNORECASE,
)
_SPLIT_CODE_TITLE_RE = re.compile(
    r"^\s*(?P<prefix>[A-Z]{2,8})[ \t]+(?P<number>\d{2,4}[A-Z]?)[ \t]+(?P<rest>.+?)\s*$",
    re.IGNORECASE,
)
_LABELLED_CREDIT_RE = re.compile(r"\bcredits?\s*[:=\-]?\s*(\d{1,3}(?:\.\d{1,2})?)\b", re.IGNORECASE)
_PAREN_CREDIT_RE = re.compile(r"\(\s*(\d{1,3}(?:\.\d{1,2})?)\s*(?:credits?|cr\.?|c)\s*\)", re.IGNORECASE)
_TRAILING_NUMBER_RE = re.compile(r"(?:\t+|\s{2,})(\d{1,3}(?:\.\d{1,2})?)\s*$")
_SKIP_LINE_RE = re.compile(
    r"^(?:subject(?:\s+code)?|course(?:\s+code|\s+title)?|sr\.?\s*no\.?|s\.?\s*no\.?|credits?|semester|unit|module|contents|index|page\s+\d+)\b",
    re.IGNORECASE,
)


def _semester_number(token: str) -> int | None:
    token = token.upper().strip()
    if token.isdigit():
        number = int(token)
        return number if 1 <= number <= 12 else None
    return _ROMAN_SEMESTERS.get(token)


def _guess_course_type(title: str) -> str:
    lower = title.lower()
    if any(word in lower for word in ("laboratory", "lab", "practical")):
        return "lab"
    if "project" in lower or "mini project" in lower:
        return "project"
    if "internship" in lower or "industrial training" in lower:
        return "internship"
    if "elective" in lower:
        return "elective"
    if "seminar" in lower:
        return "seminar"
    if "audit" in lower:
        return "audit"
    return "theory"


def _extract_credit(raw_line: str, rest: str) -> tuple[Decimal | None, str, float]:
    """Return credit, cleaned title text, and confidence contribution.

    Labelled credits are preferred. An isolated trailing numeric column is only
    a weak suggestion and stays review-required; the reviewer must confirm it.
    """
    labelled = _LABELLED_CREDIT_RE.search(rest) or _LABELLED_CREDIT_RE.search(raw_line)
    parenthetical = _PAREN_CREDIT_RE.search(rest) or _PAREN_CREDIT_RE.search(raw_line)
    match = labelled or parenthetical
    if match:
        try:
            credit = Decimal(match.group(1))
        except InvalidOperation:
            credit = None
        title = rest[:match.start()].strip(" :-,\t")
        title = re.sub(r"\(\s*\)\s*$", "", title).strip()
        return (credit if credit is not None and credit >= 0 else None), title, 0.22

    # PyMuPDF often represents separated table columns using 2+ spaces or tabs.
    trailing = _TRAILING_NUMBER_RE.search(raw_line)
    if trailing:
        try:
            guess = Decimal(trailing.group(1))
        except InvalidOperation:
            guess = Decimal("-1")
        if Decimal("0") <= guess <= Decimal("30"):
            # This is deliberately low-confidence; the UI always asks the user
            # to review it before the curriculum can be activated.
            return guess, rest.strip(), 0.08
    return None, rest.strip(), 0.0


def _parse_subject_line(raw_line: str, semester_number: int | None, page_number: int, page_text: str) -> dict[str, Any] | None:
    line = raw_line.replace("\u00a0", " ").strip()
    if not line or len(line) < 7 or len(line) > 500 or _SKIP_LINE_RE.match(line):
        return None

    match = _CODE_TITLE_RE.match(line)
    if not match:
        split_match = _SPLIT_CODE_TITLE_RE.match(line)
        if not split_match:
            return None
        code = f"{split_match.group('prefix')}{split_match.group('number')}"
        rest = split_match.group("rest")
    else:
        code = match.group("code")
        rest = match.group("rest")

    code = re.sub(r"\s+", "", code).upper().strip("./-_")
    # Codes must contain both a letter and a digit. Avoid headers, dates, and
    # body text that happen to begin with a short alpha-numeric token.
    if not code or not re.search(r"[A-Z]", code) or not re.search(r"\d", code):
        return None
    if len(code) < 3 or len(code) > 40:
        return None

    credit, title, credit_confidence = _extract_credit(line, rest)
    title = re.sub(r"\s+", " ", title).strip(" \t:;-|.")
    title = re.sub(r"\s+(?:L|T|P|TH|PR|TW|OR|IA|ESE)\s*[:=]?\s*\d+(?:\.\d+)?(?:\s+.*)?$", "", title, flags=re.IGNORECASE).strip()
    if not title or len(title) < 3 or len(title) > 220:
        return None
    if re.fullmatch(r"[\d\W]+", title) or _SKIP_LINE_RE.match(title):
        return None

    # Include concise assessment-related evidence, but don't infer maxima from
    # arbitrary numbers. Reviewers must configure a structured components list.
    evidence = []
    for candidate_line in page_text.splitlines():
        candidate_line = re.sub(r"\s+", " ", candidate_line).strip()
        if candidate_line and re.search(r"assessment|evaluation|internal|external|end semester|mid.?sem|marks distribution|practical|theory marks", candidate_line, re.IGNORECASE):
            if candidate_line not in evidence and len(candidate_line) <= 300:
                evidence.append(candidate_line)
        if len(evidence) >= 5:
            break

    confidence = 0.45 + (0.15 if semester_number else 0) + credit_confidence
    return {
        "subject_code": code,
        "title": title,
        "semester_number": semester_number,
        "credits": str(credit) if credit is not None else None,
        "course_type": _guess_course_type(title),
        "assessment_structure": {
            "components": [],
            "source_excerpt": line[:500],
            "assessment_evidence": evidence,
            "review_note": "Assessment maxima are not auto-inferred; configure components during review.",
        },
        "source_page": page_number,
        "extraction_confidence": round(min(confidence, 0.99), 3),
        "raw_line": line[:500],
    }


def extract_syllabus_pdf(pdf_bytes: bytes, max_pages: int = 250) -> dict[str, Any]:
    """Profile and extract candidate subjects from a PDF byte string.

    Only text extraction is automatic in this increment. Scanned pages are
    reported, not silently OCR'd. Extracted candidates are never official until
    manually reviewed and the curriculum version is approved and activated.
    """
    if not isinstance(pdf_bytes, (bytes, bytearray)) or not pdf_bytes:
        raise SyllabusExtractionError("The uploaded file is empty.")
    if b"%PDF-" not in bytes(pdf_bytes[:1024]):
        raise SyllabusExtractionError("The upload does not appear to be a valid PDF file.")
    try:
        import fitz
    except ImportError as exc:
        raise SyllabusExtractionError(
            "PDF extraction dependencies are missing. Run: python -m pip install -r requirements-document-processing.txt"
        ) from exc

    try:
        document = fitz.open(stream=bytes(pdf_bytes), filetype="pdf")
    except Exception as exc:
        raise SyllabusExtractionError("AcadPulse could not open this PDF. It may be damaged or unsupported.") from exc

    try:
        if document.is_encrypted:
            raise SyllabusExtractionError("Password-protected PDFs are not supported. Upload an authorized, unlocked syllabus PDF.")
        page_count = len(document)
        if page_count < 1:
            raise SyllabusExtractionError("The PDF has no pages.")
        if page_count > max_pages:
            raise SyllabusExtractionError(f"The PDF has {page_count} pages; the current safety limit is {max_pages} pages.")

        candidates: list[dict[str, Any]] = []
        page_text_counts = []
        missing_semester_lines = 0
        low_text_pages = []
        ambiguous_semester_pages = []
        duplicate_candidates = 0
        latest_semester: int | None = None
        seen: dict[tuple[int, str], dict[str, Any]] = {}

        for page_index in range(page_count):
            page = document.load_page(page_index)
            page_text = page.get_text("text") or ""
            text_chars = len(re.sub(r"\s+", "", page_text))
            page_number = page_index + 1
            page_text_counts.append(text_chars)
            if text_chars < 20:
                low_text_pages.append(page_number)

            lines = page_text.splitlines()
            page_semesters = {
                number for line in lines
                for found in [_SEMESTER_RE.search(line)] if found
                for number in [_semester_number(found.group(1))] if number is not None
            }
            # A page containing multiple semester labels is often a contents
            # page. Don't assign all rows to whichever semester appeared last.
            ambiguous_semester_page = len(page_semesters) > 1
            if ambiguous_semester_page:
                ambiguous_semester_pages.append(page_number)
                latest_semester = None
                continue
            for raw_line in lines:
                clean_line = raw_line.replace("\u00a0", " ").strip()
                semester_match = _SEMESTER_RE.search(clean_line)
                if semester_match:
                    detected = _semester_number(semester_match.group(1))
                    if detected:
                        latest_semester = detected
                candidate = _parse_subject_line(raw_line, latest_semester, page_number, page_text)
                if not candidate:
                    continue
                if candidate["semester_number"] is None:
                    missing_semester_lines += 1
                    continue
                key = (candidate["semester_number"], candidate["subject_code"].casefold())
                if key in seen:
                    duplicate_candidates += 1
                    # Prefer a duplicate with a known credit or higher confidence.
                    old = seen[key]
                    if old.get("credits") is None and candidate.get("credits") is not None:
                        old["credits"] = candidate["credits"]
                        old["extraction_confidence"] = max(old["extraction_confidence"], candidate["extraction_confidence"])
                    continue
                seen[key] = candidate
                candidates.append(candidate)

        text_pages = sum(1 for count in page_text_counts if count >= 20)
        return {
            "page_count": page_count,
            "pages_with_text": text_pages,
            "pages_with_little_or_no_text": low_text_pages,
            "ambiguous_semester_pages": ambiguous_semester_pages,
            "candidate_count": len(candidates),
            "subjects_without_semester_skipped": missing_semester_lines,
            "duplicate_candidates_skipped": duplicate_candidates,
            "candidates": candidates,
            "parser_name": "acadpulse_syllabus_text_v1",
            "parser_version": "1.0.0",
            "is_likely_scanned": text_pages == 0 or len(low_text_pages) >= max(1, page_count // 2),
        }
    finally:
        document.close()
