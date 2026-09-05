from fastapi.testclient import TestClient

from app.main import app


def _login(client: TestClient, username: str, password: str) -> dict:
    response = client.post("/api/v1/users/login", json={"username": username, "password": password})
    assert response.status_code == 200
    return response.json()["data"]


def test_protected_api_requires_access_token():
    with TestClient(app) as client:
        response = client.get("/api/v1/users/1")
    assert response.status_code == 401


def test_role_parameter_cannot_escalate_and_admin_endpoint_is_protected():
    with TestClient(app) as client:
        # 旧客户端即使提交 role=admin，也只能拿到数据库中的普通用户角色。
        response = client.post("/api/v1/users/login?username=user001&password=user12345&role=admin")
        assert response.status_code == 200
        assert response.json()["data"]["role"] == "user"
        token = response.json()["data"]["access_token"]
        assert client.get("/api/v1/users/").status_code == 401
        assert client.get("/api/v1/users/", headers={"Authorization": f"Bearer {token}"}).status_code == 403


def test_user_cannot_read_or_chat_in_another_users_session():
    with TestClient(app) as client:
        user1 = _login(client, "user001", "user12345")
        user2 = _login(client, "user002", "user12345")
        own = client.post("/api/v1/chat/sessions?user_id=1", headers={"Authorization": f"Bearer {user1['access_token']}"})
        assert own.status_code == 200
        session_id = own.json()["data"]["id"]
        other_headers = {"Authorization": f"Bearer {user2['access_token']}"}
        assert client.get(f"/api/v1/chat/history/{session_id}", headers=other_headers).status_code == 403
        assert client.post("/api/v1/chat/send", json={"user_id": 1, "session_id": session_id, "message": "越权"}, headers=other_headers).status_code == 403


def test_refresh_rotation_and_logout_revoke_access_token():
    with TestClient(app) as client:
        tokens = _login(client, "user001", "user12345")
        refreshed = client.post("/api/v1/users/refresh", json={"refresh_token": tokens["refresh_token"]})
        assert refreshed.status_code == 200
        assert refreshed.json()["data"]["refresh_token"] != tokens["refresh_token"]
        assert client.post("/api/v1/users/refresh", json={"refresh_token": tokens["refresh_token"]}).status_code == 401
        new = refreshed.json()["data"]
        assert client.post("/api/v1/users/logout", json={"refresh_token": new["refresh_token"]}, headers={"Authorization": f"Bearer {new['access_token']}"}).status_code == 200
        assert client.get("/api/v1/users/1", headers={"Authorization": f"Bearer {new['access_token']}"}).status_code == 401
