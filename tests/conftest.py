import pytest


@pytest.fixture()
def app(tmp_path):
    # Import application dependencies only for integration tests. This allows
    # dependency-free structural checks to run before the environment is set up.
    from app import create_app
    from app.extensions import db

    test_app = create_app({
        "TESTING": True,
        "SECRET_KEY": "test-only-secret-not-for-deployment",
        "WTF_CSRF_ENABLED": False,
        "SQLALCHEMY_DATABASE_URI": "sqlite://",
        "UPLOAD_ROOT": tmp_path / "uploads",
        "REPORTS_ROOT": tmp_path / "reports",
    })
    with test_app.app_context():
        db.create_all()
        yield test_app
        db.session.remove()
        db.drop_all()


@pytest.fixture()
def client(app):
    return app.test_client()
