# Existing project reference

Primary functional reference: https://github.com/shindetanay316-lab/Student-analysis-system-

Repository-level documentation reviewed for this rebuild:
- `README.md`: included modules and single-interface intent.
- `FACULTY_DECISIONS.md`: confirmed decisions and rules deliberately still pending faculty confirmation.

Features carried into the plan: student/semester/curriculum/elective/division/batch management; internal, external and lab/project marks; attendance/timetable; student/department analytics; Excel/PDF reports; result-PDF extraction, validation, student-level review/correction and confirmation.

Confirmed decisions carried forward:
- NEP/MIS is a single scheme identity.
- Curriculum changes create new immutable versions rather than editing a version used by students.
- DSE students retain their actual admission year and start college history at Semester 3 while following the matching regular cohort curriculum.
- Roll numbers change by engineering year and belong to term enrollment; previous values remain available.
- Promotion/ATKT rules remain manual until the exact approved criteria are supplied.
- One university-result PDF is scoped to one branch/program and one semester; conflicting page context must be flagged.
- Legacy Result Data migration remains disabled until it is explicitly approved.
- The DBATU Crystal parser is one supported report family, not a universal PDF parser.

This rebuild is a clean implementation. It does not import or connect to the old application's configured database.

Calculation references reviewed for formula compatibility:
- [`calculations.py`](https://github.com/shindetanay316-lab/Student-analysis-system-/blob/main/calculations.py)
- [`final_result_utils.py`](https://github.com/shindetanay316-lab/Student-analysis-system-/blob/main/final_result_utils.py)
- [`summary_utils.py`](https://github.com/shindetanay316-lab/Student-analysis-system-/blob/main/summary_utils.py)

The source-derived formula semantics and known legacy inconsistencies are recorded in `LEGACY_CALCULATION_COMPATIBILITY.md` rather than relying on memory or rewriting formulas inside routes.
