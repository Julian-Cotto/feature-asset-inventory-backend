from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import create_app


def _build_client() -> TestClient:
    get_settings.cache_clear()
    app = create_app()
    return TestClient(app)


def test_feature_items_returns_data_in_mock_mode(monkeypatch) -> None:
    monkeypatch.setenv("AUTH_MODE", "mock")

    client = _build_client()
    response = client.get("/api/inventory/it/items")

    assert response.status_code == 200
    body = response.json()
    assert body["authenticated"] is True
    assert body["user"] == "Local Dev User"
    assert "asset-inventory.view" in body["permissions"]
    assert len(body["items"]) >= 1


def test_feature_items_allows_mock_header_overrides(monkeypatch) -> None:
    monkeypatch.setenv("AUTH_MODE", "mock")
    monkeypatch.setenv("AUTH_DEBUG_HEADERS_ENABLED", "true")

    client = _build_client()
    response = client.get(
        "/api/inventory/it/items",
        headers={
            "X-Debug-User-Id": "boris",
            "X-Debug-User-Name": "Boris Moshkovich",
            "X-Debug-Email": "boris@example.com",
            "X-Debug-Roles": "admin,developer",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["authenticated"] is True
    assert body["user"] == "Boris Moshkovich"
    assert "*" in body["permissions"]
    assert len(body["items"]) >= 1


def test_feature_items_rejects_mock_user_without_required_permission(monkeypatch) -> None:
    monkeypatch.setenv("AUTH_MODE", "mock")
    monkeypatch.setenv("AUTH_DEBUG_HEADERS_ENABLED", "true")

    client = _build_client()
    response = client.get(
        "/api/inventory/it/items",
        headers={
            "X-Debug-User-Name": "Unprivileged User",
            "X-Debug-Roles": "unmapped-role",
        },
    )

    assert response.status_code == 403
    assert response.json()["detail"] == "You do not have permission to access this resource."


def test_feature_items_requires_bearer_token_in_entra_mode(monkeypatch) -> None:
    monkeypatch.setenv("AUTH_MODE", "entra")
    monkeypatch.setenv("ENTRA_TENANT_ID", "11111111-1111-1111-1111-111111111111")
    monkeypatch.setenv("ENTRA_CLIENT_ID", "22222222-2222-2222-2222-222222222222")

    client = _build_client()
    response = client.get("/api/inventory/it/items")

    assert response.status_code == 401
    assert response.json()["detail"] == "Bearer token is required."


def test_feature_items_accepts_valid_entra_token_when_validator_is_mocked(monkeypatch) -> None:
    monkeypatch.setenv("AUTH_MODE", "entra")
    monkeypatch.setenv("ENTRA_TENANT_ID", "11111111-1111-1111-1111-111111111111")
    monkeypatch.setenv("ENTRA_CLIENT_ID", "22222222-2222-2222-2222-222222222222")

    from app.platform import auth_context as auth_module

    def _fake_validate_entra_token(raw_token: str):
        assert raw_token == "valid-test-token"
        return {
            "sub": "user-123",
            "name": "Test User",
            "preferred_username": "test.user@example.com",
            "roles": ["developer", "reader"],
            "groups": ["group-a"],
            "scp": "access_as_user",
            "iss": "https://login.microsoftonline.com/11111111-1111-1111-1111-111111111111/v2.0",
            "aud": "22222222-2222-2222-2222-222222222222",
            "iat": 1700000000,
            "exp": 4700000000,
        }

    monkeypatch.setattr(auth_module, "_validate_entra_token", _fake_validate_entra_token)

    client = _build_client()
    response = client.get(
        "/api/inventory/it/items",
        headers={"Authorization": "Bearer valid-test-token"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["authenticated"] is True
    assert body["user"] == "Test User"
    assert "asset-inventory.view" in body["permissions"]
    assert len(body["items"]) >= 1


def test_feature_items_rejects_entra_token_without_required_permission(monkeypatch) -> None:
    monkeypatch.setenv("AUTH_MODE", "entra")
    monkeypatch.setenv("ENTRA_TENANT_ID", "11111111-1111-1111-1111-111111111111")
    monkeypatch.setenv("ENTRA_CLIENT_ID", "22222222-2222-2222-2222-222222222222")
    monkeypatch.setenv("AUTH_SCOPE_PERMISSION_MAP_RAW", "")

    from app.platform import auth_context as auth_module

    def _fake_validate_entra_token(raw_token: str):
        assert raw_token == "valid-test-token"
        return {
            "sub": "user-123",
            "name": "Test User",
            "preferred_username": "test.user@example.com",
            "roles": [],
            "groups": [],
            "scp": "access_as_user",
            "iss": "https://login.microsoftonline.com/11111111-1111-1111-1111-111111111111/v2.0",
            "aud": "22222222-2222-2222-2222-222222222222",
            "iat": 1700000000,
            "exp": 4700000000,
        }

    monkeypatch.setattr(auth_module, "_validate_entra_token", _fake_validate_entra_token)

    client = _build_client()
    response = client.get(
        "/api/inventory/it/items",
        headers={"Authorization": "Bearer valid-test-token"},
    )

    assert response.status_code == 403
    assert response.json()["detail"] == "You do not have permission to access this resource."


def test_admin_check_allows_admin_in_mock_mode(monkeypatch) -> None:
    monkeypatch.setenv("AUTH_MODE", "mock")
    monkeypatch.setenv("AUTH_DEBUG_HEADERS_ENABLED", "true")

    client = _build_client()
    response = client.get(
        "/api/inventory/it/admin-check",
        headers={
            "X-Debug-User-Name": "Admin User",
            "X-Debug-Roles": "admin,developer",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["user"] == "Admin User"
    assert "admin" in body["roles"]
    assert "*" in body["permissions"]


def test_admin_check_rejects_non_admin_in_mock_mode(monkeypatch) -> None:
    monkeypatch.setenv("AUTH_MODE", "mock")
    monkeypatch.setenv("AUTH_DEBUG_HEADERS_ENABLED", "true")

    client = _build_client()
    response = client.get(
        "/api/inventory/it/admin-check",
        headers={
            "X-Debug-User-Name": "Reader User",
            "X-Debug-Roles": "reader",
        },
    )

    assert response.status_code == 403
    assert response.json()["detail"] == "You do not have permission to access this resource."