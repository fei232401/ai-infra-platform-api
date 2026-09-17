from datetime import datetime, timedelta, timezone

import pytest

pytestmark = pytest.mark.integration

BASE_TIME = "2026-03-01T10:00:00+00:00"


async def create_model(client, name: str = "qwen2.5:0.5b") -> dict:
    response = await client.post(
        "/api/v1/models",
        json={"name": name, "family": "qwen", "parameter_billions": "0.5", "quantization": "q4_K_M"},
    )
    assert response.status_code == 201, response.text
    return response.json()


async def create_backend(client, model_id: int, name: str = "ollama-local", **overrides) -> dict:
    payload = {
        "name": name,
        "url": "http://127.0.0.1:11434",
        "engine": "ollama",
        "weight": "1.5",
        "max_concurrency": 2,
        "cost_per_token": "0",
        "model_ids": [model_id],
    }
    payload.update(overrides)
    response = await client.post("/api/v1/backends", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


async def test_healthz(client) -> None:
    response = await client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


async def test_readyz_reports_schema_version(client) -> None:
    response = await client.get("/readyz")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["database"] == "up"
    assert body["schema_version"] == 1
    assert body["failed"] == []
    assert body["pool"]["size"] >= 1


async def test_model_create_and_conflict(client) -> None:
    created = await create_model(client)
    assert created["parameter_billions"] == 0.5

    duplicate = await client.post("/api/v1/models", json={"name": "qwen2.5:0.5b"})
    assert duplicate.status_code == 409
    assert duplicate.json()["code"] == "conflict"


async def test_model_upsert_is_idempotent(client) -> None:
    first = await client.post("/api/v1/models/upsert", json={"name": "m", "family": "qwen"})
    second = await client.post(
        "/api/v1/models/upsert", json={"name": "m", "family": "qwen", "quantization": "fp16"}
    )
    assert first.status_code == 200
    assert first.json()["id"] == second.json()["id"]
    assert second.json()["quantization"] == "fp16"


async def test_model_missing_returns_404(client) -> None:
    response = await client.get("/api/v1/models/424242")
    assert response.status_code == 404
    assert response.json()["code"] == "not_found"


async def test_model_list_pagination_meta(client) -> None:
    for index in range(5):
        await create_model(client, name=f"model-{index}")
    response = await client.get("/api/v1/models", params={"limit": 2, "offset": 1})
    body = response.json()
    assert body["meta"] == {"limit": 2, "offset": 1, "total": 5, "count": 2, "has_more": True}

    last = await client.get("/api/v1/models", params={"limit": 2, "offset": 4})
    assert last.json()["meta"]["has_more"] is False


async def test_page_size_is_clamped(client) -> None:
    response = await client.get("/api/v1/models", params={"limit": 10_000})
    assert response.json()["meta"]["limit"] == 200


async def test_page_size_must_be_positive(client) -> None:
    response = await client.get("/api/v1/models", params={"limit": 0})
    assert response.status_code == 422
    assert response.json()["code"] == "request_invalid"


async def test_backend_register_and_expand_models(client) -> None:
    model = await create_model(client)
    backend = await create_backend(client, model["id"])
    assert backend["weight"] == 1.5
    assert backend["state"] == "active"

    detail = await client.get(f"/api/v1/backends/{backend['id']}", params={"with_models": True})
    assert [item["id"] for item in detail.json()["models"]] == [model["id"]]

    listed = await client.get("/api/v1/backends", params={"with_models": True})
    body = listed.json()
    assert body["meta"]["total"] == 1
    assert body["items"][0]["models"][0]["name"] == "qwen2.5:0.5b"


async def test_backend_duplicate_name_conflict(client) -> None:
    model = await create_model(client)
    await create_backend(client, model["id"])
    response = await client.post(
        "/api/v1/backends",
        json={"name": "ollama-local", "url": "http://127.0.0.1:11435", "engine": "ollama"},
    )
    assert response.status_code == 409


async def test_backend_engine_check_constraint(client) -> None:
    response = await client.post(
        "/api/v1/backends",
        json={"name": "weird", "url": "http://127.0.0.1:9999", "engine": "tgi"},
    )
    assert response.status_code == 422


async def test_backend_patch_and_soft_delete(client) -> None:
    model = await create_model(client)
    backend = await create_backend(client, model["id"])

    patched = await client.patch(f"/api/v1/backends/{backend['id']}", json={"weight": "4"})
    assert patched.status_code == 200
    assert patched.json()["weight"] == 4.0

    deleted = await client.delete(f"/api/v1/backends/{backend['id']}")
    assert deleted.status_code == 200
    assert deleted.json()["state"] == "disabled"

    still_there = await client.get(f"/api/v1/backends/{backend['id']}")
    assert still_there.status_code == 200


async def test_backend_invalid_state_transition(client) -> None:
    model = await create_model(client)
    backend = await create_backend(client, model["id"])
    response = await client.post(
        f"/api/v1/backends/{backend['id']}/state", json={"state": "active"}
    )
    assert response.status_code == 409
    assert response.json()["detail"]["from"] == "active"


async def test_backend_bind_and_replace_models(client) -> None:
    first = await create_model(client, name="m-1")
    second = await create_model(client, name="m-2")
    backend = await create_backend(client, first["id"])

    bound = await client.post(
        f"/api/v1/backends/{backend['id']}/models", json={"model_ids": [second["id"]]}
    )
    assert bound.status_code == 200
    assert bound.json()["inserted"] == 1

    replaced = await client.put(
        f"/api/v1/backends/{backend['id']}/models", json={"model_ids": [second["id"]]}
    )
    assert replaced.json()["removed"] == 1
    assert replaced.json()["inserted"] == 0

    unbound = await client.request(
        "DELETE",
        f"/api/v1/backends/{backend['id']}/models",
        json={"model_ids": [second["id"]]},
    )
    assert unbound.json()["removed"] == 1


async def test_backend_health_roundtrip(client) -> None:
    model = await create_model(client)
    backend = await create_backend(client, model["id"])
    response = await client.post(
        f"/api/v1/backends/{backend['id']}/health",
        json={"healthy": True, "latency_ms": 240},
    )
    assert response.status_code == 201
    assert response.json()["latency_ms"] == 240

    history = await client.get(f"/api/v1/backends/{backend['id']}/health")
    assert len(history.json()) == 1


async def test_backend_missing_model_id_returns_404(client) -> None:
    response = await client.post(
        "/api/v1/backends",
        json={
            "name": "ghost",
            "url": "http://127.0.0.1:11434",
            "engine": "ollama",
            "model_ids": [987_654],
        },
    )
    assert response.status_code == 404
    assert response.json()["detail"]["missing_model_ids"] == [987_654]


async def test_routing_candidates_endpoint(client) -> None:
    model = await create_model(client)
    fast = await create_backend(client, model["id"], name="fast", url="http://127.0.0.1:11434")
    slow = await create_backend(
        client, model["id"], name="slow", url="http://127.0.0.1:11435", weight="1"
    )
    await client.post(f"/api/v1/backends/{fast['id']}/health", json={"healthy": True, "latency_ms": 120})
    await client.post(f"/api/v1/backends/{slow['id']}/health", json={"healthy": True, "latency_ms": 1800})

    response = await client.get(
        "/api/v1/routing/candidates",
        params={"model_name": model["name"], "policy": "least_latency"},
    )
    assert response.status_code == 200
    ranked = response.json()["ranked"]
    assert [item["name"] for item in ranked] == ["fast", "slow"]
    assert ranked[0]["score"] > ranked[1]["score"]


async def test_routing_candidates_rejects_unknown_policy(client) -> None:
    model = await create_model(client)
    response = await client.get(
        "/api/v1/routing/candidates",
        params={"model_name": model["name"], "policy": "magic"},
    )
    assert response.status_code == 422


async def test_request_log_with_decision(client) -> None:
    model = await create_model(client)
    backend = await create_backend(client, model["id"])
    payload = {
        "model_name": model["name"],
        "status": "success",
        "started_at": BASE_TIME,
        "finished_at": "2026-03-01T10:00:01+00:00",
        "backend_id": backend["id"],
        "prompt_tokens": 10,
        "completion_tokens": 40,
        "ttft_ms": "110.0",
        "total_ms": "1000.0",
        "decision": {
            "policy": "weighted_random",
            "candidate_ids": [backend["id"]],
            "chosen_id": backend["id"],
            "score_snapshot": {"backend_id": backend["id"], "score": 0.75},
        },
    }
    created = await client.post("/api/v1/requests", json=payload)
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["decision"]["chosen_id"] == backend["id"]
    assert body["ttft_ms"] == 110.0

    fetched = await client.get(f"/api/v1/requests/{body['request_id']}")
    assert fetched.status_code == 200
    assert fetched.json()["decision"]["policy"] == "weighted_random"


async def test_request_log_invalid_status(client) -> None:
    model = await create_model(client)
    backend = await create_backend(client, model["id"])
    response = await client.post(
        "/api/v1/requests",
        json={
            "model_name": model["name"],
            "status": "cancelled",
            "started_at": BASE_TIME,
            "finished_at": "2026-03-01T10:00:01+00:00",
            "backend_id": backend["id"],
        },
    )
    assert response.status_code == 422
    assert response.json()["code"] == "validation_failed"


async def test_request_log_foreign_key_mapped_to_422(client) -> None:
    response = await client.post(
        "/api/v1/requests",
        json={
            "model_name": "qwen2.5:0.5b",
            "status": "success",
            "started_at": BASE_TIME,
            "finished_at": "2026-03-01T10:00:01+00:00",
            "backend_id": 987_654,
        },
    )
    assert response.status_code == 422, response.text
    assert response.json()["code"] == "foreign_key_violation"


async def test_request_log_duplicate_request_id_conflicts(client) -> None:
    model = await create_model(client)
    backend = await create_backend(client, model["id"])
    payload = {
        "model_name": model["name"],
        "status": "success",
        "started_at": BASE_TIME,
        "finished_at": "2026-03-01T10:00:01+00:00",
        "backend_id": backend["id"],
        "request_id": "11111111-2222-3333-4444-555555555555",
    }
    first = await client.post("/api/v1/requests", json=payload)
    assert first.status_code == 201
    duplicate = await client.post("/api/v1/requests", json=payload)
    assert duplicate.status_code == 409
    assert duplicate.json()["code"] == "conflict"


async def test_request_list_filters_and_summary(client) -> None:
    model = await create_model(client)
    backend = await create_backend(client, model["id"])
    await client.post(
        "/api/v1/requests",
        json={
            "model_name": model["name"],
            "status": "success",
            "started_at": BASE_TIME,
            "finished_at": "2026-03-01T10:00:01+00:00",
            "backend_id": backend["id"],
            "total_ms": "500.0",
        },
    )
    await client.post(
        "/api/v1/requests",
        json={
            "model_name": model["name"],
            "status": "error",
            "error_code": "upstream_500",
            "started_at": "2026-03-01T10:05:00+00:00",
            "finished_at": "2026-03-01T10:05:01+00:00",
            "backend_id": backend["id"],
            "total_ms": "700.0",
        },
    )

    failures = await client.get("/api/v1/requests", params={"failures_only": True})
    assert failures.json()["meta"]["total"] == 1

    summary = await client.get("/api/v1/requests/summary")
    body = summary.json()
    assert {(item["status"], item["total"]) for item in body["by_status"]} == {
        ("success", 1),
        ("error", 1),
    }
    assert body["latency"]["samples"] == 2
    assert body["by_backend"][0]["backend_id"] == backend["id"]


async def test_request_purge(client) -> None:
    model = await create_model(client)
    backend = await create_backend(client, model["id"])
    for offset_days in range(2):
        started = datetime(2026, 3, 1, tzinfo=timezone.utc) + timedelta(days=offset_days)
        await client.post(
            "/api/v1/requests",
            json={
                "model_name": model["name"],
                "status": "success",
                "started_at": started.isoformat(),
                "finished_at": (started + timedelta(milliseconds=100)).isoformat(),
                "backend_id": backend["id"],
            },
        )
    response = await client.post(
        "/api/v1/requests/purge",
        json={"cutoff": "2026-03-02T00:00:00+00:00", "batch_size": 100},
    )
    assert response.status_code == 200
    assert response.json()["deleted"] == 1


async def test_api_key_lifecycle(client) -> None:
    created = await client.post(
        "/api/v1/keys", json={"name": "ci-key", "scopes": ["infer", "admin"], "ttl_days": 30}
    )
    assert created.status_code == 201, created.text
    body = created.json()
    plaintext = body["key"]
    assert plaintext.startswith("aip_")
    assert body["key_prefix"] == plaintext[:8]
    assert "key_hash" not in body

    verified = await client.post("/api/v1/keys/verify", json={"key": plaintext})
    assert verified.json()["valid"] is True

    invalid = await client.post("/api/v1/keys/verify", json={"key": "aip_not-a-real-key-value"})
    assert invalid.json() == {"valid": False, "api_key": None}

    revoked = await client.post(f"/api/v1/keys/{body['id']}/revoke")
    assert revoked.status_code == 200
    assert revoked.json()["revoked_at"] is not None

    after = await client.post("/api/v1/keys/verify", json={"key": plaintext})
    assert after.json()["valid"] is False


async def test_api_key_invalid_scope_rejected(client) -> None:
    response = await client.post("/api/v1/keys", json={"name": "bad", "scopes": ["root"]})
    assert response.status_code == 422
    assert response.json()["code"] == "validation_failed"


async def test_api_key_list_hides_hash(client) -> None:
    await client.post("/api/v1/keys", json={"name": "k1"})
    response = await client.get("/api/v1/keys")
    body = response.json()
    assert body["meta"]["total"] == 1
    assert "key_hash" not in body["items"][0]
    assert body["items"][0]["scopes"] == ["infer"]
