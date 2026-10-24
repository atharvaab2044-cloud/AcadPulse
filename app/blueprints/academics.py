"""Minimum academic programme registry needed to support student enrollments."""
import re

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from ..extensions import db
from ..models import AcademicProgram, AuditEvent
from ..services.security import roles_required

bp = Blueprint("academics", __name__)
PROGRAM_CODE_RE = re.compile(r"^[A-Z0-9][A-Z0-9_-]{1,23}$")


@bp.get("/programs")
@login_required
def programs():
    rows = db.session.execute(
        select(AcademicProgram).order_by(AcademicProgram.is_active.desc(), AcademicProgram.name.asc())
    ).scalars().all()
    return render_template("programs.html", page_title="Programme registry", programs=rows)


@bp.route("/programs/new", methods=["GET", "POST"])
@roles_required("admin")
def new_program():
    form_data = request.form if request.method == "POST" else {}
    if request.method == "POST":
        code = (request.form.get("code") or "").strip().upper()
        name = " ".join((request.form.get("name") or "").split())
        degree = " ".join((request.form.get("degree") or "B.E.").split()) or "B.E."
        try:
            duration_years = int(request.form.get("duration_years", "4"))
        except ValueError:
            duration_years = 0
        errors = []
        if not PROGRAM_CODE_RE.fullmatch(code):
            errors.append("Programme code must be 2–24 characters and contain only letters, numbers, hyphens or underscores.")
        if not name or len(name) > 160:
            errors.append("Programme name is required and must be 160 characters or fewer.")
        if not degree or len(degree) > 40:
            errors.append("Degree must be 1–40 characters.")
        if not 1 <= duration_years <= 10:
            errors.append("Programme duration must be between 1 and 10 years.")
        duplicate = db.session.scalar(select(AcademicProgram.id).where(func.lower(AcademicProgram.code) == code.lower())) if code else None
        if duplicate:
            errors.append("A programme with this code already exists.")
        if errors:
            for error in errors:
                flash(error, "error")
            return render_template("program_form.html", page_title="Add programme", form_data=form_data), 400

        program = AcademicProgram(code=code, name=name, degree=degree, duration_years=duration_years, is_active=True)
        db.session.add(program)
        try:
            db.session.flush()
            db.session.add(AuditEvent(
                actor_id=current_user.id,
                action="academic_program_created",
                entity_type="academic_program",
                entity_id=str(program.id),
                after_json={"code": program.code, "name": program.name, "degree": program.degree, "duration_years": program.duration_years},
            ))
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            flash("The programme code conflicts with an existing record. No change was saved.", "error")
            return render_template("program_form.html", page_title="Add programme", form_data=form_data), 409
        flash(f"Programme {program.code} created. You can now enroll students in it.", "success")
        return redirect(url_for("academics.programs"))
    return render_template("program_form.html", page_title="Add programme", form_data=form_data)
