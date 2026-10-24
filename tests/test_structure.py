"""Dependency-free checks that can run before Flask dependencies are installed."""
from pathlib import Path
import ast
import re
import unittest

ROOT = Path(__file__).resolve().parents[1]


class ProjectStructureTests(unittest.TestCase):
    def test_required_files_exist(self):
        required = [
            "run.py", "requirements.txt", ".env.example", "app/__init__.py",
            "app/models.py", "app/blueprints/auth.py", "app/blueprints/main.py",
            "app/blueprints/students.py", "app/blueprints/academics.py", "app/blueprints/curricula.py",
            "app/templates/student_form.html", "app/templates/student_detail.html",
            "app/templates/programs.html", "app/templates/program_form.html",
            "app/templates/curriculum_detail.html", "app/templates/curriculum_subject_form.html",
            "app/services/syllabus_extractor.py", "app/services/curriculum_workflow.py",
            "tests/test_syllabus_extractor.py", "tests/test_curriculum_workflow.py",
            "tests/test_curriculum_routes.py", "docs/PHASE_2_SYLLABUS_CURRICULUM.md",
            "tests/test_student_management.py", "docs/PHASE_3_STUDENT_ENROLLMENT.md",
            "app/services/legacy_calculations.py", "app/services/legacy_mark_service.py",
            "app/services/legacy_assessment_summary.py", "app/services/academic_calculations.py",
            "app/blueprints/coursework.py", "app/blueprints/attendance.py", "app/blueprints/results.py",
            "app/blueprints/electives.py", "app/blueprints/system.py", "app/blueprints/reports.py",
            "app/blueprints/calculations.py", "app/templates/calculations.html",
            "app/templates/calculation_detail.html", "app/templates/cgpa_detail.html", "app/templates/promotion_detail.html",
            "tests/test_calculation_workflows.py", "app/services/spreadsheet_imports.py",
            "tests/test_spreadsheet_imports_service.py", "tests/test_bulk_import_workflows.py",
            "docs/MVP_SCOPE_AND_LIMITATIONS.md", "docs/BUILD_VALIDATION.md",
            "app/templates/base.html", "app/static/css/app.css",
            "docs/IMPLEMENTATION_PLAN.md", "docs/REQUIREMENTS_AND_CONSTRAINTS.md",
            "docs/PHASE_WISE_ROADMAP.md", "docs/LEGACY_CALCULATION_COMPATIBILITY.md",
        ]
        for relative in required:
            with self.subTest(path=relative):
                self.assertTrue((ROOT / relative).is_file(), relative)

    def test_secret_file_is_ignored(self):
        ignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
        self.assertIn(".env", ignore)

    def test_models_encode_identity_and_history_boundaries(self):
        models = (ROOT / "app/models.py").read_text(encoding="utf-8")
        self.assertIn('class StudentIdentifier', models)
        self.assertIn('class StudentEnrollment', models)
        self.assertIn('class CurriculumVersion', models)
        self.assertIn('class StagedResult', models)
        self.assertIn('class ExamAttempt', models)
        self.assertIn('class AcademicPolicy', models)
        self.assertIn('class AuditEvent', models)
        self.assertIn('UniqueConstraint("student_id", "academic_year", "semester_number"', models)

    def test_branding_uses_acadpulse(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        layout = (ROOT / "app/templates/base.html").read_text(encoding="utf-8")
        self.assertIn("AcadPulse", readme)
        self.assertIn("AcadPulse", layout)
        self.assertNotIn("Acad" + "Metrics", layout)

    def test_student_management_route_enforces_history_and_verification(self):
        source = (ROOT / "app/blueprints/students.py").read_text(encoding="utf-8")
        self.assertIn("@roles_required(\"admin\")", source)
        self.assertIn("verification_status=\"pending\"", source)
        self.assertIn("student_enrollment_created", source)
        self.assertIn("must start at Semester 3", source)
        self.assertIn("Existing records are not overwritten", source)
        self.assertIn('_anchor="add-enrollment"', source)
        self.assertIn('enrollment_form_data=form_data, status=400', source)
        self.assertIn('enrollment_form_data=form_data, status=409', source)
        template = (ROOT / "app/templates/student_detail.html").read_text(encoding="utf-8")
        self.assertIn('id="add-enrollment"', template)
        self.assertIn('id="enrollment-history"', template)
        self.assertIn("Choose semester", template)

    def test_legacy_calculation_profile_is_versioned_and_referenced(self):
        service = (ROOT / "app/services/legacy_mark_service.py").read_text(encoding="utf-8")
        source = (ROOT / "app/services/legacy_calculations.py").read_text(encoding="utf-8")
        docs = (ROOT / "docs/LEGACY_CALCULATION_COMPATIBILITY.md").read_text(encoding="utf-8")
        self.assertIn("dbatu_ece_legacy_calculations_v1", service)
        self.assertIn("def compute_final_result", source)
        self.assertIn("highest two", docs)

    def test_all_primary_workspaces_are_registered(self):
        factory = (ROOT / "app/__init__.py").read_text(encoding="utf-8")
        for blueprint in ("coursework_bp", "attendance_bp", "results_bp", "electives_bp", "system_bp", "reports_bp", "calculations_bp"):
            with self.subTest(blueprint=blueprint):
                self.assertIn(f"register_blueprint({blueprint})", factory)
        layout = (ROOT / "app/templates/base.html").read_text(encoding="utf-8")
        self.assertIn("url_for('calculations.index')", layout)

    def test_effective_attempt_requires_policy_and_reason(self):
        source = (ROOT / "app/blueprints/results.py").read_text(encoding="utf-8")
        self.assertIn('policy_type == "supplementary"', source)
        self.assertIn('effective_attempt_selection") == "admin_selected"', source)
        self.assertIn("len(reason) < 25", source)
        self.assertIn("effective_for_calculation = (other.id == attempt.id)", source)

    def test_calculation_snapshots_are_policy_gated_and_append_only(self):
        source = (ROOT / "app/blueprints/calculations.py").read_text(encoding="utf-8")
        self.assertIn('snapshot.status != "calculated"', source)
        self.assertIn('snapshot.status = "approved"', source)
        self.assertIn('old.status = "superseded"', source)
        self.assertIn('Enrollment status was not changed automatically.', source)

    def test_every_template_url_for_references_a_declared_route(self):
        route_endpoints = set()
        for source_path in (ROOT / "app" / "blueprints").glob("*.py"):
            source = source_path.read_text(encoding="utf-8")
            tree = ast.parse(source, filename=str(source_path))
            blueprint_name = None
            for node in ast.walk(tree):
                if not isinstance(node, ast.Assign):
                    continue
                if not any(isinstance(target, ast.Name) and target.id == "bp" for target in node.targets):
                    continue
                value = node.value
                if isinstance(value, ast.Call) and isinstance(value.func, ast.Name) and value.func.id == "Blueprint" and value.args:
                    if isinstance(value.args[0], ast.Constant):
                        blueprint_name = value.args[0].value
                        break
            if not blueprint_name:
                continue
            for node in ast.walk(tree):
                if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                if any(
                    isinstance(decorator, ast.Call)
                    and isinstance(decorator.func, ast.Attribute)
                    and isinstance(decorator.func.value, ast.Name)
                    and decorator.func.value.id == "bp"
                    and decorator.func.attr in {"route", "get", "post", "put", "delete", "patch"}
                    for decorator in node.decorator_list
                ):
                    route_endpoints.add(f"{blueprint_name}.{node.name}")
        template_endpoints = set()
        for template_path in (ROOT / "app" / "templates").rglob("*.html"):
            content = template_path.read_text(encoding="utf-8")
            template_endpoints.update(re.findall(r"url_for\(['\"]([^'\"]+)['\"]", content))
        missing = sorted(template_endpoints - route_endpoints - {"static"})
        self.assertEqual(missing, [], f"Template links reference undeclared endpoints: {missing}")

    def test_no_legacy_database_or_real_credential_is_configured(self):
        config = (ROOT / "app/config.py").read_text(encoding="utf-8")
        self.assertNotIn("Atharva8788", config)
        self.assertNotIn("ece_sms", config)

    def test_xlsx_export_escapes_formula_like_strings(self):
        reports = (ROOT / "app/blueprints/reports.py").read_text(encoding="utf-8")
        self.assertIn("sheet.append([_text_safe(row[key]) for key, _ in COLUMNS])", reports)
        coursework = (ROOT / "app/blueprints/coursework.py").read_text(encoding="utf-8")
        attendance = (ROOT / "app/blueprints/attendance.py").read_text(encoding="utf-8")
        self.assertIn("_safe_csv_value(value)", coursework)
        self.assertIn("_safe_csv_value(value)", attendance)
        self.assertIn("correction reason of at least 8 characters", coursework)
        self.assertIn("correction reason of at least 8 characters", attendance)


if __name__ == "__main__":
    unittest.main()
