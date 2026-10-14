import pytest
from fastapi.testclient import TestClient

from schrift.config import Settings
from schrift.context import AppContext
from schrift.db import Database
from schrift.main import create_app
from schrift.migrate import upgrade
from schrift.models import User
from schrift.security import hash_password


@pytest.fixture
def settings(tmp_path):
    return Settings(
        data_dir=tmp_path / "data",
        secret_key="test",
        base_url="http://localhost",
        semantic_search=False,
    )


@pytest.fixture
def ctx(settings):
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    db = Database(f"sqlite:///{settings.data_dir / 'schrift.db'}")
    upgrade(db.engine)
    return AppContext(settings=settings, db=db)


@pytest.fixture
def session(ctx):
    with ctx.db.session() as s:
        yield s


@pytest.fixture
def user(session):
    u = User(username="cas", password_hash=hash_password("geheim-wachtwoord"), is_admin=True)
    session.add(u)
    session.flush()
    return u


@pytest.fixture
def vault(ctx, session, user):
    return ctx.vault(session, user)


@pytest.fixture
def client(settings):
    app = create_app(settings)
    with TestClient(app) as c:
        yield c


@pytest.fixture(autouse=True)
def reset_login_limiter():
    from schrift.web.common import login_limiter

    login_limiter.hits.clear()
    yield
    login_limiter.hits.clear()
