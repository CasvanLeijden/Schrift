def test_healthz(client):
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_pages_require_login(client):
    response = client.get("/", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"].startswith("/login")
    assert client.get("/api/notes/x").status_code == 401


def test_login_page_renders(client):
    response = client.get("/login")
    assert response.status_code == 200
    assert "Inloggen" in response.text


def test_manifest_and_service_worker(client):
    assert client.get("/manifest.webmanifest").json()["name"] == "Schrift"
    assert client.get("/sw.js").status_code == 200


def test_head_requests(client):
    assert client.head("/login").status_code == 200
    assert client.head("/healthz").content == b""
