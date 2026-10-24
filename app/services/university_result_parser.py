"""Conservative generic text-PDF recognizer for staging university result rows.

This is intentionally not advertised as a universal parser. It extracts only
recognizable subject-code-like lines and preserves the whole source line/page;
marks and identity links must be reviewed before an attempt is created.
"""
from __future__ import annotations
import re
import fitz

PRN_LABEL = re.compile(r"\b(?:PRN(?:\s*(?:NO|NUMBER))?|PERMANENT\s+REGISTRATION\s+NUMBER)\s*[:#\-]?\s*([A-Z0-9][A-Z0-9/\-]{5,23})", re.I)
SUBJECT_CODE = re.compile(r"(?<![A-Z0-9])([A-Z]{2,8}[-/]?\d{2,5}[A-Z0-9./-]{0,8})(?![A-Z0-9])", re.I)


class ResultPdfError(ValueError):
    pass


def normalize_identifier(value: str | None) -> str | None:
    if not value:
        return None
    result = "".join(str(value).upper().split())
    return result or None


def extract_candidate_rows(pdf_bytes: bytes, max_pages: int = 300, max_rows: int = 1000) -> dict:
    """Return source-backed candidate rows. Never infer authoritative marks.

    Numeric tokens are stored as candidates only: different university layouts
    assign different meanings to them, so they are not automatically mapped to
    internal/external/total marks here.
    """
    try:
        document = fitz.open(stream=pdf_bytes, filetype="pdf")
    except Exception as exc:
        raise ResultPdfError("The file is not a readable PDF or is password protected.") from exc
    if document.is_encrypted:
        document.close()
        raise ResultPdfError("Password-protected PDFs are not supported. Upload an unlocked PDF.")
    if document.page_count == 0:
        document.close()
        raise ResultPdfError("The PDF contains no pages.")
    if document.page_count > max_pages:
        document.close()
        raise ResultPdfError(f"The PDF has more than {max_pages} pages. Split it into smaller files.")
    rows = []
    page_profiles = []
    try:
        for page_index, page in enumerate(document, start=1):
            text = page.get_text("text") or ""
            page_profiles.append({"page": page_index, "characters": len(text.strip())})
            if not text.strip():
                continue
            page_prns = [normalize_identifier(match.group(1)) for match in PRN_LABEL.finditer(text)]
            page_prn = page_prns[0] if page_prns else None
            for raw_line in text.splitlines():
                line = " ".join(raw_line.split()).strip()
                if not line or len(line) > 300:
                    continue
                code_match = SUBJECT_CODE.search(line)
                if not code_match:
                    continue
                code = code_match.group(1).upper().rstrip(".-/")
                remainder = (line[:code_match.start()] + " " + line[code_match.end():]).strip(" -|:\t")
                # Ignore common headings and rows without enough descriptive context.
                if not remainder or len(remainder) < 3 or remainder.lower() in {"subject", "subject code", "total", "result"}:
                    continue
                numeric_tokens = re.findall(r"(?<![A-Za-z])\d+(?:\.\d+)?", remainder)
                title = remainder
                rows.append({
                    "page_number": page_index,
                    "raw_payload": {"source_line": line, "numeric_tokens_unmapped": numeric_tokens,
                                    "extractor_note": "Candidate only; confirm field mapping in the review screen."},
                    "extracted_prn": page_prn,
                    "normalized_prn": page_prn,
                    "extracted_subject_code": code,
                    "extracted_subject_title": title[:220],
                    "extracted_marks": {},
                    "extracted_grade": None,
                    "extracted_result_status": None,
                    "confidence": None,
                    "review_status": "pending",
                    "match_status": "unmatched",
                })
                if len(rows) >= max_rows:
                    break
            if len(rows) >= max_rows:
                break
    finally:
        document.close()
    selectable_chars = sum(item["characters"] for item in page_profiles)
    return {"rows": rows, "page_profiles": page_profiles, "page_count": len(page_profiles),
            "selectable_characters": selectable_chars, "scanned_or_image_only_pages": [p["page"] for p in page_profiles if p["characters"] < 20],
            "parser_name": "generic_subject_line_candidate_parser", "parser_version": "0.1.0"}
