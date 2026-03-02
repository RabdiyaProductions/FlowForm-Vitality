def test_api_health_contract(client):
    response = client.get("/api/health")

    assert response.status_code == 200
    payload = response.get_json()

    assert payload == {
        "status": "healthy",
        "service": "flowform-app",
        "ai": {"enabled": True, "reason": None},
    }


def test_ready_contains_sections(client):
    response = client.get("/ready")

    assert response.status_code == 200
    payload = response.get_json()

    assert payload["status"] == "ready"
    assert "db" in payload
    assert "module" in payload
    assert "log" in payload
    assert payload["db"]["initialized"] is True
