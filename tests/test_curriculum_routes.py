"""Integration tests for draft-only syllabus ingestion and controlled activation."""
from io import BytesIO
import json

import fitz
from sqlalchemy import select

from app.extensions import db
from app.models import (
    AcademicProgram, AuditEvent, CurriculumScheme, CurriculumSubject,
    CurriculumVersion, User,
)


def _admin():
    user = User(email="curriculum-admin@example.test", full_name="Curriculum Admin", role="admin")
    user.set_password("Curriculum-Test-Password-123!")
    return user


def _program():
    return AcademicProgram(code="ECE", name="Electronics and Computer Engineering", degree="B.E.", duration_years=4)


def _pdf(*subject_lines):
    document = fitz.open()
    page = document.new_page()
    page.insert_text((72, 60), "SEMESTER I", fontsize=12)
    y = 90
    for line in subject_lines:
        page.insert_text((72, y), line, fontsize=10)
        y += 22
    data = document.tobytes()
    document.close()
    return data


def _login(client, app):
    with app.app_context():
        user = _admin()
        db.session.add(user)
        db.session.commit()
    return client.post("/login", data={"email": "curriculum-admin@example.test", "password": "Curriculum-Test-Password-123!"}, follow_redirects=True)


def _upload(client, program_id, pdf_bytes, **extra):
    data = {
        "program_id": str(program_id),
        "effective_cohort_year": "2026",
        "new_scheme_code": "NEP_MIS",
        "new_scheme_name": "NEP/MIS",
        "syllabus_pdf": (BytesIO(pdf_bytes), "official-syllabus.pdf", "application/pdf"),
    }
    data.update(extra)
    return client.post("/curriculum/upload", data=data, content_type="multipart/form-data")


def _verify_subject(client, version_id, subject_id, code, title, credits, course_type="theory"):
    structure = {
        "components": [
            {"name": "Internal", "max_marks": 40},
            {"name": "External", "max_marks": 60},
        ],
        "total_max": 100,
    }
    return client.post(f"/curriculum/{version_id}/subjects/{subject_id}/edit", data={
        "subject_code": code,
        "title": title,
        "semester_number": "1",
        "credits": str(credits),
        "course_type": course_type,
        "assessment_structure_json": json.dumps(structure),
        "review_status": "verified",
        "notes": "Verified against the official syllabus table.",
        "source_page": "1",
    }, follow_redirects=True)


def _complete_approval(client, version_id, app):
    response = client.post(f"/curriculum/{version_id}/submit-review", follow_redirects=True)
    assert response.status_code == 200
    response = client.post(f"/curriculum/{version_id}/approve", data={"approval_note": "Checked against the approved source document."}, follow_redirects=True)
    assert response.status_code == 200
    response = client.post(f"/curriculum/{version_id}/activate", follow_redirects=True)
    assert response.status_code == 200


def test_upload_creates_review_required_draft_and_keeps_it_inactive(client, app):
    _login(client, app)
    with app.app_context():
        program = _program()
        db.session.add(program)
        db.session.commit()
        program_id = program.id

    response = _upload(client, program_id, _pdf(
        "BSC101 Engineering Mathematics I Credits: 4",
        "BSC102 Engineering Physics Credits: 3",
    ))
    assert response.status_code == 302
    assert "/curriculum/" in response.headers["Location"]
    with app.app_context():
        version = db.session.scalar(select(CurriculumVersion))
        subjects = db.session.execute(select(CurriculumSubject).where(CurriculumSubject.curriculum_version_id == version.id)).scalars().all()
        assert version.status == "draft"
        assert version.source_sha256 and len(version.source_sha256) == 64
        assert version.source_storage_key.startswith("syllabus/")
        assert len(subjects) == 2
        assert all(row.review_status == "review_required" for row in subjects)
        assert all(not (row.assessment_structure or {}).get("components") for row in subjects)
        assert db.session.scalar(select(CurriculumVersion.id).where(CurriculumVersion.status == "active")) is None
        assert db.session.scalar(select(AuditEvent).where(AuditEvent.action == "syllabus_uploaded_curriculum_draft_created")) is not None


def test_full_draft_review_approval_activation_and_revision_retirement(client, app):
    _login(client, app)
    with app.app_context():
        program = _program()
        db.session.add(program)
        db.session.commit()
        program_id = program.id

    response = _upload(client, program_id, _pdf(
        "BSC101 Engineering Mathematics I Credits: 4",
        "BSC102 Engineering Physics Credits: 3",
    ))
    version_id = int(response.headers["Location"].rstrip("/").split("/")[-1])
    with app.app_context():
        subjects = db.session.execute(select(CurriculumSubject).where(CurriculumSubject.curriculum_version_id == version_id).order_by(CurriculumSubject.subject_code)).scalars().all()
        subject_data = [(row.id, row.subject_code, row.title, row.credits) for row in subjects]
    for sid, code, title, credits in subject_data:
        _verify_subject(client, version_id, sid, code, title, credits)
    _complete_approval(client, version_id, app)

    with app.app_context():
        old_version = db.session.get(CurriculumVersion, version_id)
        assert old_version.status == "active"
        old_scheme_id = old_version.scheme_id

    response = _upload(client, program_id, _pdf(
        "BSC101 Engineering Mathematics I Credits: 4",
        "BSC102 Applied Physics Credits: 3",
        "BSC103 Programming Laboratory Credits: 1",
    ), scheme_id=str(old_scheme_id), compare_version_id=str(version_id))
    assert response.status_code == 302
    new_version_id = int(response.headers["Location"].split("/curriculum/")[1].split("?")[0])
    assert f"compare={version_id}" in response.headers["Location"]
    detail = client.get(response.headers["Location"])
    assert detail.status_code == 200
    assert b"Revision comparison" in detail.data
    assert b"Added subjects" in detail.data
    assert b"Changed subjects" in detail.data

    with app.app_context():
        new_subjects = db.session.execute(select(CurriculumSubject).where(CurriculumSubject.curriculum_version_id == new_version_id).order_by(CurriculumSubject.subject_code)).scalars().all()
        new_subject_data = [(row.id, row.subject_code, row.title, row.credits) for row in new_subjects]
    for sid, code, title, credits in new_subject_data:
        _verify_subject(client, new_version_id, sid, code, title, credits)
    _complete_approval(client, new_version_id, app)

    with app.app_context():
        old_version = db.session.get(CurriculumVersion, version_id)
        new_version = db.session.get(CurriculumVersion, new_version_id)
        assert old_version.status == "retired"
        assert new_version.status == "active"
        old_codes = db.session.execute(select(CurriculumSubject.subject_code).where(CurriculumSubject.curriculum_version_id == version_id)).scalars().all()
        assert set(old_codes) == {"BSC101", "BSC102"}
        assert db.session.scalar(select(AuditEvent.id).where(AuditEvent.action == "curriculum_version_retired_by_new_activation")) is not None


def test_same_pdf_for_same_scope_is_detected_without_duplicate_version(client, app):
    _login(client, app)
    with app.app_context():
        program = _program()
        db.session.add(program)
        db.session.commit()
        program_id = program.id
    pdf = _pdf("BSC101 Engineering Mathematics Credits: 4")
    first = _upload(client, program_id, pdf)
    second = _upload(client, program_id, pdf)
    assert first.status_code == 302 and second.status_code == 302
    with app.app_context():
        versions = db.session.execute(select(CurriculumVersion)).scalars().all()
        assert len(versions) == 1


def test_bad_pdf_does_not_create_version(client, app):
    _login(client, app)
    with app.app_context():
        program = _program()
        db.session.add(program)
        db.session.commit()
        program_id = program.id
    response = _upload(client, program_id, b"This is not PDF data")
    assert response.status_code == 302
    with app.app_context():
        assert db.session.scalar(select(CurriculumVersion.id)) is None


def test_unapproved_or_unready_version_cannot_be_activated(client, app):
    _login(client, app)
    with app.app_context():
        program = _program()
        scheme = CurriculumScheme(code="NEP_MIS", display_name="NEP/MIS")
        db.session.add_all([program, scheme])
        db.session.flush()
        version = CurriculumVersion(version_code="ECE_NEP_2026", program_id=program.id, scheme_id=scheme.id, effective_cohort_year=2026, status="draft")
        db.session.add(version)
        db.session.commit()
        version_id = version.id
    response = client.post(f"/curriculum/{version_id}/activate", follow_redirects=True)
    assert response.status_code == 200
    with app.app_context():
        assert db.session.get(CurriculumVersion, version_id).status == "draft"


def test_non_admin_cannot_upload_or_modify_curriculum(client, app):
    with app.app_context():
        user = User(email="faculty-curriculum@example.test", full_name="Faculty Member", role="faculty")
        user.set_password("Faculty-Test-Password-123!")
        program = _program()
        db.session.add_all([user, program])
        db.session.commit()
        program_id = program.id
    client.post("/login", data={"email": "faculty-curriculum@example.test", "password": "Faculty-Test-Password-123!"}, follow_redirects=True)
    response = _upload(client, program_id, _pdf("BSC101 Engineering Mathematics Credits: 4"))
    assert response.status_code == 403
