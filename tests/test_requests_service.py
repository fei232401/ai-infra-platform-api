from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from src.domain import ConflictError, NotFoundError, ValidationError
from src.service import BackendService, RequestLogService

pytestmark = pytest.mark.integration

BASE_TIME = datetime(2026, 2, 1, 12, 0, 0, tzinfo=timezone.utc)


async def make_backend(session, seeded_models, name: str = "b1"):
    service = BackendService(session)
    return await service.register(
        name=name,
        url=f"http://127.0.0.1:{11434 + len(name)}",
        engine="ollama",
        weight=1,
        max_concurrency=1,
        cost_per_token=0,
        model_ids=[seeded_models[0]],
    )


def success_payload(backend_id: int, **overrides) -> dict:
    payload = {
        "model_name": "qwen2.5:0.5b",
        "status": "success",
        "started_at": BASE_TIME,
        "finished_at": BASE_TIME + timedelta(milliseconds=800),
        "backend_id": backend_id,
        "prompt_tokens": 12,
        "completion_tokens": 48,
        "ttft_ms": "120.5",
        "total_ms": "800.0",
    }
    payload.update(overrides)
    return payload


def decision_payload(chosen_id: int, **overrides) -> dict:
    payload = {
        "policy": "weighted_random",
        "candidate_ids": [chosen_id],
        "chosen_id": chosen_id,
        "score_snapshot": {"backend_id": chosen_id, "score": 0.75},
        "fallback_reason": None,
    }
    payload.update(overrides)
    return payload


async def test_record_success_with_decision(session, seeded_models) -> None:
    backend = await make_backend(session, seeded_models)
    service = RequestLogService(session)
    entity, decision = await service.record(
        **success_payload(backend.id),
        decision=decision_payload(backend.id),
    )
    assert entity.status == "success"
    assert entity.prompt_tokens == 12
    assert float(entity.ttft_ms) == pytest.approx(120.5)
    assert decision is not None
    assert decision.chosen_id == backend.id
    assert decision.candidate_ids == [backend.id]


async def test_record_rejects_success_with_error_code(session, seeded_models) -> None:
    backend = await make_backend(session, seeded_models)
    service = RequestLogService(session)
    with pytest.raises(ValidationError):
        await service.record(**success_payload(backend.id, error_code="boom"))


async def test_record_requires_error_code_on_failure(session, seeded_models) -> None:
    backend = await make_backend(session, seeded_models)
    service = RequestLogService(session)
    with pytest.raises(ValidationError):
        await service.record(**success_payload(backend.id, status="error"))

    entity, _ = await service.record(
        **success_payload(backend.id, status="timeout", error_code="upstream_timeout")
    )
    assert entity.status == "timeout"
    assert entity.error_code == "upstream_timeout"


async def test_record_requires_finished_at(session, seeded_models) -> None:
    backend = await make_backend(session, seeded_models)
    service = RequestLogService(session)
    with pytest.raises(ValidationError):
        await service.record(**success_payload(backend.id, finished_at=None))


async def test_record_rejects_backwards_timeline(session, seeded_models) -> None:
    backend = await make_backend(session, seeded_models)
    service = RequestLogService(session)
    with pytest.raises(ValidationError):
        await service.record(
            **success_payload(backend.id, finished_at=BASE_TIME - timedelta(seconds=1))
        )


async def test_record_rejects_negative_tokens(session, seeded_models) -> None:
    backend = await make_backend(session, seeded_models)
    service = RequestLogService(session)
    with pytest.raises(ValidationError):
        await service.record(**success_payload(backend.id, prompt_tokens=-1))


async def test_record_rejects_unknown_status(session, seeded_models) -> None:
    backend = await make_backend(session, seeded_models)
    service = RequestLogService(session)
    with pytest.raises(ValidationError):
        await service.record(**success_payload(backend.id, status="cancelled"))


async def test_duplicate_request_id_conflicts(session, seeded_models) -> None:
    backend = await make_backend(session, seeded_models)
    service = RequestLogService(session)
    request_id = uuid4()
    await service.record(**success_payload(backend.id, request_id=request_id))
    with pytest.raises(ConflictError):
        await service.record(**success_payload(backend.id, request_id=request_id))


async def test_decision_chosen_must_be_candidate(session, seeded_models) -> None:
    first = await make_backend(session, seeded_models, name="b1")
    second = await make_backend(session, seeded_models, name="b2")
    service = RequestLogService(session)
    with pytest.raises(ValidationError):
        await service.record(
            **success_payload(first.id),
            decision=decision_payload(
                second.id, candidate_ids=[first.id], chosen_id=second.id
            ),
        )


async def test_decision_rejects_unknown_policy(session, seeded_models) -> None:
    backend = await make_backend(session, seeded_models)
    service = RequestLogService(session)
    with pytest.raises(ValidationError):
        await service.record(
            **success_payload(backend.id),
            decision=decision_payload(backend.id, policy="magic"),
        )


async def test_get_returns_decision(session, seeded_models) -> None:
    backend = await make_backend(session, seeded_models)
    service = RequestLogService(session)
    entity, _ = await service.record(
        **success_payload(backend.id),
        decision=decision_payload(backend.id),
    )
    fetched, decision = await service.get(entity.request_id)
    assert fetched.request_id == entity.request_id
    assert decision is not None
    assert decision.policy == "weighted_random"


async def test_get_missing_request_raises(session, seeded_models) -> None:
    service = RequestLogService(session)
    with pytest.raises(NotFoundError):
        await service.get(uuid4())


async def test_list_filters(session, seeded_models) -> None:
    backend = await make_backend(session, seeded_models)
    service = RequestLogService(session)
    await service.record(**success_payload(backend.id, session_id="s-1"))
    await service.record(
        **success_payload(
            backend.id,
            status="error",
            error_code="upstream_500",
            session_id="s-1",
            started_at=BASE_TIME + timedelta(seconds=10),
            finished_at=BASE_TIME + timedelta(seconds=10, milliseconds=200),
        )
    )

    items, total = await service.list()
    assert total == 2

    items, total = await service.list(status="success")
    assert total == 1

    items, total = await service.list(failures_only=True)
    assert total == 1
    assert items[0].error_code == "upstream_500"

    items, total = await service.list(session_id="s-1")
    assert total == 2

    items, total = await service.list(session_id="nope")
    assert total == 0

    items, total = await service.list(started_after=BASE_TIME + timedelta(seconds=5))
    assert total == 1


async def test_summary_aggregates(session, seeded_models) -> None:
    first = await make_backend(session, seeded_models, name="b1")
    second = await make_backend(session, seeded_models, name="b2")
    service = RequestLogService(session)
    await service.record(**success_payload(first.id, total_ms="100.0", ttft_ms="10.0"))
    await service.record(
        **success_payload(
            first.id,
            status="error",
            error_code="upstream_500",
            total_ms="300.0",
            ttft_ms="30.0",
            started_at=BASE_TIME + timedelta(seconds=1),
            finished_at=BASE_TIME + timedelta(seconds=1, milliseconds=300),
        )
    )
    await service.record(
        **success_payload(
            second.id,
            total_ms="900.0",
            ttft_ms="90.0",
            started_at=BASE_TIME + timedelta(seconds=2),
            finished_at=BASE_TIME + timedelta(seconds=2, milliseconds=900),
        )
    )

    summary = await service.summary()
    by_backend = {item["backend_id"]: item for item in summary["by_backend"]}
    assert by_backend[first.id]["total"] == 2
    assert by_backend[first.id]["successes"] == 1
    assert by_backend[first.id]["failures"] == 1
    assert by_backend[second.id]["total"] == 1

    by_status = {item["status"]: item["total"] for item in summary["by_status"]}
    assert by_status == {"success": 2, "error": 1}

    latency = summary["latency"]
    assert latency["samples"] == 3
    assert latency["p50_ms"] == pytest.approx(300.0)
    assert latency["max_ms"] == pytest.approx(900.0)


async def test_purge_deletes_only_finished_before_cutoff(session, seeded_models) -> None:
    backend = await make_backend(session, seeded_models)
    service = RequestLogService(session)
    for index in range(3):
        started = BASE_TIME + timedelta(days=index)
        await service.record(
            **success_payload(
                backend.id,
                started_at=started,
                finished_at=started + timedelta(milliseconds=100),
            )
        )

    deleted = await service.purge_finished_before(BASE_TIME + timedelta(days=1))
    assert deleted == 1

    _, total = await service.list()
    assert total == 2
