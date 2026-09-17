from datetime import datetime, timedelta, timezone

import pytest

from src.domain import ConflictError, NotFoundError, ValidationError
from src.service import BackendService

pytestmark = pytest.mark.integration

BASE_TIME = datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc)


async def register(
    service: BackendService,
    *,
    name: str,
    url: str,
    engine: str = "ollama",
    weight: str = "1",
    model_ids: list[int] | None = None,
):
    return await service.register(
        name=name,
        url=url,
        engine=engine,
        weight=weight,
        max_concurrency=1,
        cost_per_token=0,
        model_ids=model_ids or [],
    )


async def test_register_and_bind_models(session, seeded_models) -> None:
    service = BackendService(session)
    entity = await register(
        service,
        name="ollama-local",
        url="http://127.0.0.1:11434",
        model_ids=[seeded_models[0], seeded_models[1]],
    )
    assert entity.id > 0
    assert entity.state == "active"
    models = await service.list_models(entity.id)
    assert [item.id for item in models] == [seeded_models[0], seeded_models[1]]


async def test_duplicate_name_conflicts(session, seeded_models) -> None:
    service = BackendService(session)
    await register(service, name="dup", url="http://127.0.0.1:11434")
    with pytest.raises(ConflictError):
        await register(service, name="dup", url="http://127.0.0.1:11435")


async def test_duplicate_url_conflicts(session, seeded_models) -> None:
    service = BackendService(session)
    await register(service, name="first", url="http://127.0.0.1:11434")
    with pytest.raises(ConflictError):
        await register(service, name="second", url="http://127.0.0.1:11434")


async def test_invalid_engine_rejected(session, seeded_models) -> None:
    service = BackendService(session)
    with pytest.raises(ValidationError):
        await register(service, name="bad", url="http://127.0.0.1:11434", engine="tgi")


async def test_weight_must_be_positive(session, seeded_models) -> None:
    service = BackendService(session)
    with pytest.raises(ValidationError):
        await register(service, name="bad", url="http://127.0.0.1:11434", weight="0")


async def test_unknown_model_id_rejected(session, seeded_models) -> None:
    service = BackendService(session)
    with pytest.raises(NotFoundError) as excinfo:
        await register(
            service,
            name="bad",
            url="http://127.0.0.1:11434",
            model_ids=[seeded_models[0], 999_999],
        )
    assert excinfo.value.detail["missing_model_ids"] == [999_999]


async def test_state_transitions_are_enforced(session, seeded_models) -> None:
    service = BackendService(session)
    entity = await register(service, name="b1", url="http://127.0.0.1:11434")

    with pytest.raises(ConflictError):
        await service.change_state(entity.id, "active")

    draining = await service.change_state(entity.id, "draining")
    assert draining.state == "draining"

    disabled = await service.change_state(entity.id, "disabled")
    assert disabled.state == "disabled"

    with pytest.raises(ConflictError):
        await service.change_state(entity.id, "draining")

    revived = await service.change_state(entity.id, "active")
    assert revived.state == "active"


async def test_update_rejects_empty_payload(session, seeded_models) -> None:
    service = BackendService(session)
    entity = await register(service, name="b1", url="http://127.0.0.1:11434")
    with pytest.raises(ValidationError):
        await service.update(entity.id, {})


async def test_update_url_conflict(session, seeded_models) -> None:
    service = BackendService(session)
    await register(service, name="b1", url="http://127.0.0.1:11434")
    second = await register(service, name="b2", url="http://127.0.0.1:11435")
    with pytest.raises(ConflictError):
        await service.update(second.id, {"url": "http://127.0.0.1:11434"})


async def test_health_history_returns_newest_first(session, seeded_models) -> None:
    service = BackendService(session)
    entity = await register(service, name="b1", url="http://127.0.0.1:11434")
    for index in range(3):
        await service.record_health(
            entity.id,
            healthy=True,
            latency_ms=100 + index,
            checked_at=BASE_TIME + timedelta(seconds=index),
        )
    history = await service.health_history(entity.id, limit=2)
    assert [item.latency_ms for item in history] == [102, 101]


async def test_rank_candidates_orders_by_latency(session, seeded_models) -> None:
    service = BackendService(session)
    fast = await register(
        service, name="fast", url="http://127.0.0.1:11434", model_ids=[seeded_models[0]]
    )
    slow = await register(
        service, name="slow", url="http://127.0.0.1:11435", model_ids=[seeded_models[0]]
    )
    await service.record_health(fast.id, healthy=True, latency_ms=200, checked_at=BASE_TIME)
    await service.record_health(
        slow.id, healthy=True, latency_ms=1600, checked_at=BASE_TIME + timedelta(seconds=1)
    )

    ranked = await service.rank_candidates("qwen2.5:0.5b", "least_latency")
    assert [item.backend_id for item in ranked] == [fast.id, slow.id]


async def test_rank_candidates_excludes_unhealthy(session, seeded_models) -> None:
    service = BackendService(session)
    flaky = await register(
        service, name="flaky", url="http://127.0.0.1:11434", model_ids=[seeded_models[0]]
    )
    steady = await register(
        service, name="steady", url="http://127.0.0.1:11435", model_ids=[seeded_models[0]]
    )
    await service.record_health(
        flaky.id, healthy=True, latency_ms=100, checked_at=BASE_TIME
    )
    await service.record_health(
        flaky.id,
        healthy=False,
        error="connection refused",
        checked_at=BASE_TIME + timedelta(seconds=5),
    )
    await service.record_health(
        steady.id, healthy=True, latency_ms=900, checked_at=BASE_TIME + timedelta(seconds=1)
    )

    ranked = await service.rank_candidates("qwen2.5:0.5b", "least_latency")
    assert [item.backend_id for item in ranked] == [steady.id, flaky.id]
    assert ranked[1].excluded is True


async def test_rank_candidates_ignores_disabled_backends(session, seeded_models) -> None:
    service = BackendService(session)
    entity = await register(
        service, name="b1", url="http://127.0.0.1:11434", model_ids=[seeded_models[0]]
    )
    await service.change_state(entity.id, "disabled")
    ranked = await service.rank_candidates("qwen2.5:0.5b", "weighted_random")
    assert ranked == []


async def test_rank_candidates_unknown_model_returns_empty(session, seeded_models) -> None:
    service = BackendService(session)
    ranked = await service.rank_candidates("does-not-exist", "weighted_random")
    assert ranked == []


async def test_replace_models_reports_diff(session, seeded_models) -> None:
    service = BackendService(session)
    entity = await register(
        service,
        name="b1",
        url="http://127.0.0.1:11434",
        model_ids=[seeded_models[0], seeded_models[1]],
    )
    result = await service.replace_models(entity.id, [seeded_models[1], seeded_models[2]])
    assert result["inserted"] == 1
    assert result["removed"] == 1
    assert result["model_ids"] == [seeded_models[1], seeded_models[2]]

    repeat = await service.replace_models(entity.id, [seeded_models[1], seeded_models[2]])
    assert repeat["inserted"] == 0
    assert repeat["removed"] == 0


async def test_bulk_register_is_idempotent(session, seeded_models) -> None:
    service = BackendService(session)
    items = [
        {"name": "b1", "url": "http://127.0.0.1:11434", "engine": "ollama"},
        {"name": "b2", "url": "http://127.0.0.1:11435", "engine": "ollama"},
    ]
    first = await service.bulk_register(items)
    assert first == {"submitted": 2, "inserted": 2, "skipped": 0}

    second = await service.bulk_register(items)
    assert second == {"submitted": 2, "inserted": 0, "skipped": 2}


async def test_list_filters_and_total(session, seeded_models) -> None:
    service = BackendService(session)
    await register(service, name="ollama-a", url="http://127.0.0.1:11434")
    await register(service, name="ollama-b", url="http://127.0.0.1:11435")
    remote = await register(
        service, name="openai-a", url="https://api.example.com/v1", engine="openai"
    )
    await service.change_state(remote.id, "disabled")

    items, total = await service.list(engine="ollama")
    assert total == 2
    assert {item.name for item in items} == {"ollama-a", "ollama-b"}

    items, total = await service.list(state="disabled")
    assert total == 1
    assert items[0].name == "openai-a"

    items, total = await service.list(name_like="OLLAMA")
    assert total == 2

    items, total = await service.list(name_like="ollama", limit=1, offset=1)
    assert total == 2
    assert len(items) == 1


async def test_list_rejects_invalid_engine(session, seeded_models) -> None:
    service = BackendService(session)
    with pytest.raises(ValidationError):
        await service.list(engine="vllm")


async def test_get_missing_backend_raises(session, seeded_models) -> None:
    service = BackendService(session)
    with pytest.raises(NotFoundError):
        await service.get(999_999)
