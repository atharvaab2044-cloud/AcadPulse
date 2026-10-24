"""AcadPulse application factory."""
import os
import secrets
from pathlib import Path
from datetime import datetime

import click
from dotenv import load_dotenv
from flask import Flask, current_app
from flask.cli import with_appcontext

from .extensions import csrf, db, login_manager, migrate

load_dotenv()


def create_app(test_config=None):
    project_root = Path(__file__).resolve().parent.parent
    instance_path = project_root / "instance"
    instance_path.mkdir(parents=True, exist_ok=True)

    app = Flask(__name__, instance_relative_config=True, instance_path=str(instance_path))
    app.config.from_object("app.config.Config")
    app.config["SQLALCHEMY_DATABASE_URI"] = app.config.get("SQLALCHEMY_DATABASE_URI") or "sqlite:///acadpulse_dev.db"
    app.config["SQLALCHEMY_ENGINE_OPTIONS"] = app.config.get("SQLALCHEMY_ENGINE_OPTIONS") or {"pool_pre_ping": True}
    app.config["UPLOAD_ROOT"] = Path(app.config["UPLOAD_ROOT"]).resolve()
    app.config["REPORTS_ROOT"] = Path(app.config["REPORTS_ROOT"]).resolve()

    if test_config:
        app.config.update(test_config)
    app.config["UPLOAD_ROOT"] = Path(app.config["UPLOAD_ROOT"]).resolve()
    app.config["REPORTS_ROOT"] = Path(app.config["REPORTS_ROOT"]).resolve()
    app.config["UPLOAD_ROOT"].mkdir(parents=True, exist_ok=True)
    app.config["REPORTS_ROOT"].mkdir(parents=True, exist_ok=True)
    secret_key = app.config.get("SECRET_KEY")
    if not secret_key or secret_key == "replace-with-a-long-random-secret":
        if app.config.get("TESTING"):
            app.config["SECRET_KEY"] = "test-only-secret-not-for-deployment"
        elif app.config.get("APP_ENV", "development") == "development":
            app.config["SECRET_KEY"] = "local-dev-" + secrets.token_hex(32)
            app.logger.warning("SECRET_KEY is not configured with a persistent value. Using a temporary local-development key; update .env before normal use.")
        else:
            raise RuntimeError("SECRET_KEY must be configured outside local development. See .env.example.")

    db.init_app(app)
    login_manager.init_app(app)
    csrf.init_app(app)
    migrate.init_app(app, db)

    # Import models so Flask-Migrate can discover their metadata.
    from . import models  # noqa: F401
    from .blueprints.auth import bp as auth_bp
    from .blueprints.main import bp as main_bp
    from .blueprints.students import bp as students_bp
    from .blueprints.academics import bp as academics_bp
    from .blueprints.curricula import bp as curricula_bp
    from .blueprints.coursework import bp as coursework_bp
    from .blueprints.attendance import bp as attendance_bp
    from .blueprints.results import bp as results_bp
    from .blueprints.electives import bp as electives_bp
    from .blueprints.system import bp as system_bp
    from .blueprints.reports import bp as reports_bp
    from .blueprints.calculations import bp as calculations_bp
    app.register_blueprint(auth_bp)
    app.register_blueprint(main_bp)
    app.register_blueprint(students_bp)
    app.register_blueprint(academics_bp)
    app.register_blueprint(curricula_bp)
    app.register_blueprint(coursework_bp)
    app.register_blueprint(attendance_bp)
    app.register_blueprint(results_bp)
    app.register_blueprint(electives_bp)
    app.register_blueprint(system_bp)
    app.register_blueprint(reports_bp)
    app.register_blueprint(calculations_bp)

    @app.errorhandler(403)
    def forbidden(_error):
        from flask import render_template
        return render_template("errors/403.html", page_title="Access denied"), 403

    @app.context_processor
    def inject_template_globals():
        hour = datetime.now().hour
        greeting = "morning" if hour < 12 else "afternoon" if hour < 18 else "evening"
        return {"now_year": datetime.now().year, "greeting": greeting}

    app.cli.add_command(init_db_command)
    app.cli.add_command(create_admin_command)
    app.cli.add_command(seed_demo_command)
    return app


@click.command("init-db")
@with_appcontext
def init_db_command():
    """Create tables for a throwaway/local development database only."""
    if current_app.config.get("APP_ENV") != "development":
        raise click.ClickException("init-db is disabled outside development. Use reviewed Flask-Migrate migrations.")
    db.create_all()
    click.echo("Development database tables created. Use Flask-Migrate migrations for any shared or production database.")


@click.command("create-admin")
@click.option("--email", prompt=True, help="Admin email address")
@click.option("--name", prompt="Full name", help="Admin display name")
@click.password_option(confirmation_prompt=True, help="Use at least 12 characters")
@with_appcontext
def create_admin_command(email, name, password):
    """Create an administrator without a default password."""
    from .models import User
    email = email.strip().lower()
    existing = db.session.scalar(db.select(User).where(User.email == email))
    if existing:
        raise click.ClickException("An account with that email already exists.")
    user = User(email=email, full_name=name.strip(), role="admin", is_active=True)
    try:
        user.set_password(password)
    except ValueError as exc:
        raise click.ClickException(str(exc)) from exc
    db.session.add(user)
    db.session.commit()
    click.echo(f"Administrator created for {email}.")


@click.command("seed-demo")
@click.confirmation_option(prompt="Insert clearly synthetic demo academic records into this database?")
@with_appcontext
def seed_demo_command():
    """Insert only fictional demo records for local UI exploration."""
    if current_app.config.get("APP_ENV") != "development":
        raise click.ClickException("seed-demo is disabled outside development.")
    from datetime import date
    from decimal import Decimal
    from .models import AcademicProgram, CurriculumScheme, CurriculumVersion, CurriculumSubject, Student, StudentEnrollment

    program = db.session.scalar(db.select(AcademicProgram).where(AcademicProgram.code == "DEMO-ECE"))
    if not program:
        program = AcademicProgram(code="DEMO-ECE", name="Electronics & Computer Engineering (Demo)", degree="B.E.", duration_years=4)
        db.session.add(program)
        db.session.flush()
    scheme = db.session.scalar(db.select(CurriculumScheme).where(CurriculumScheme.code == "NEP_MIS"))
    if not scheme:
        scheme = CurriculumScheme(code="NEP_MIS", display_name="NEP/MIS", description="Single scheme identity per faculty decision")
        db.session.add(scheme)
        db.session.flush()
    version = db.session.scalar(db.select(CurriculumVersion).where(CurriculumVersion.version_code == "DEMO_NEP_MIS_2026"))
    if not version:
        version = CurriculumVersion(version_code="DEMO_NEP_MIS_2026", scheme_id=scheme.id, program_id=program.id, effective_cohort_year=2026, status="draft")
        db.session.add(version)
        db.session.flush()
        for semester, code, title, credits, kind in [
            (1, "DEMO101", "Engineering Mathematics I", "4.00", "theory"),
            (1, "DEMO102", "Engineering Physics", "3.00", "theory"),
            (1, "DEMO103", "Basic Electrical Engineering Lab", "1.00", "lab"),
            (2, "DEMO201", "Engineering Mathematics II", "4.00", "theory"),
            (2, "DEMO202", "Programming Fundamentals", "3.00", "theory"),
            (2, "DEMO203", "Programming Lab", "1.00", "lab"),
        ]:
            db.session.add(CurriculumSubject(curriculum_version_id=version.id, semester_number=semester, subject_code=code, title=title, credits=Decimal(credits), course_type=kind, assessment_structure={"components": [], "review_note": "Synthetic demo subject; configure assessment structure before approval."}, review_status="review_required"))
    names = ["Aarav Demo", "Diya Sample", "Ishaan Example", "Mira Fiction", "Kabir Test", "Anaya Sample", "Reyansh Demo", "Sara Example"]
    for i, name in enumerate(names, start=1):
        code = f"DEMO-2026-{i:03d}"
        student = db.session.scalar(db.select(Student).where(Student.student_code == code))
        if not student:
            student = Student(student_code=code, full_name=name, status="active")
            db.session.add(student)
            db.session.flush()
            db.session.add(StudentEnrollment(student_id=student.id, program_id=program.id, curriculum_version_id=None, academic_year="2026-27", semester_number=1, roll_number=f"{i:02d}", division="A", practical_batch=f"A{(i-1)//4+1}", enrollment_type="regular", enrollment_status="enrolled", admission_year=2026, curriculum_cohort_year=2026, enrolled_at=date(2026, 7, 1)))
    db.session.commit()
    click.echo("Synthetic demo records inserted. They are fictional and should not be treated as real student data.")
