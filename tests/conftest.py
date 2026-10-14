import pytest
from fastapi.testclient import TestClient

from schrift.config import Settings
from schrift.main import create_app


@pytest.fixture
def client(tmp_path):
    app = create_app(Settings(data_dir=tmp_path / "data", secret_key="test"))
    with TestClient(app) as c:
        yield c
