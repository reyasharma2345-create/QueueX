def test_csrf_endpoint_returns_token(client):
    response = client.get("/api/csrf-token")
    assert response.status_code == 200
    assert response.get_json()["csrf_token"]


def test_post_without_csrf_token_is_rejected(raw_client):
    response = raw_client.post("/api/auth/logout", json={})
    assert response.status_code == 400
    assert "csrf" in response.get_json()["error"].lower()


def test_post_with_invalid_csrf_token_is_rejected(raw_client):
    response = raw_client.post(
        "/api/auth/logout",
        json={},
        headers={"X-CSRFToken": "invalid-token"},
    )
    assert response.status_code == 400
    assert "csrf" in response.get_json()["error"].lower()


def test_post_with_valid_csrf_token_passes_csrf_check(raw_client):
    token_response = raw_client.get("/api/csrf-token")
    assert token_response.status_code == 200
    token = token_response.get_json()["csrf_token"]

    response = raw_client.post(
        "/api/auth/logout",
        json={},
        headers={"X-CSRFToken": token},
    )
    assert response.status_code != 400
