"""Previous-repository internal-assessment summary thresholds.

These thresholds are for descriptive CT/assessment summary reports only. They
are not the final theory pass rule, ESE minimum, or an official progression rule.
"""

PASS_THRESHOLD = {
    "CT1": 4,
    "CT2": 4,
    "MIDSEM": 8,
    "ASSIGNMENT": 4,
}

EXAM_COLUMN_MAP = {
    "CT1": "ct1",
    "CT2": "ct2",
    "MIDSEM": "midsem",
    "ASSIGNMENT": "assignment",
}

SEMESTER_CLASS_LABEL = {
    1: "First Year",
    2: "First Year",
    3: "Second Year",
    4: "Second Year",
    5: "Third Year",
    6: "Third Year",
    7: "Final Year",
    8: "Final Year",
}


def summary_column(exam_type: str) -> str:
    """Match the old report helper's fallback to CT1 for unknown types."""
    return EXAM_COLUMN_MAP.get((exam_type or "").upper(), "ct1")


def classify_summary_mark(exam_type: str, mark):
    """Return `absent`, `passed` or `failed` using legacy summary thresholds."""
    if mark is None or str(mark).strip() == "":
        return "absent"
    value = float(mark)
    threshold = PASS_THRESHOLD.get((exam_type or "").upper(), 4)
    return "passed" if value >= threshold else "failed"
