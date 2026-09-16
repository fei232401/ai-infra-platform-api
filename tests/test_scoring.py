import json
from decimal import Decimal

import pytest

from src.service.scoring import CandidateScore, pick_by_weight, score_candidates


def candidate(
    backend_id: int,
    name: str,
    weight: str = "1",
    healthy: bool | None = True,
    latency_ms: int | None = None,
    cost: str = "0",
) -> dict[str, object]:
    return {
        "backend_id": backend_id,
        "name": name,
        "weight": Decimal(weight),
        "healthy": healthy,
        "latency_ms": latency_ms,
        "cost_per_token": Decimal(cost),
    }


def test_unknown_policy_raises() -> None:
    with pytest.raises(ValueError, match="unknown policy"):
        score_candidates("magic_policy", [candidate(1, "b1")])


def test_weight_drives_order_for_weighted_random() -> None:
    ranked = score_candidates(
        "weighted_random",
        [candidate(1, "light", weight="1"), candidate(2, "heavy", weight="2")],
    )
    assert [item.backend_id for item in ranked] == [2, 1]
    assert ranked[0].score == pytest.approx(1.0)
    assert ranked[1].score == pytest.approx(0.5)


def test_unhealthy_candidate_is_excluded_with_zero_score() -> None:
    ranked = score_candidates(
        "weighted_random",
        [candidate(1, "sick", healthy=False), candidate(2, "fine", healthy=True)],
    )
    sick = next(item for item in ranked if item.backend_id == 1)
    assert sick.excluded is True
    assert sick.score == 0.0
    assert ranked[0].backend_id == 2


def test_unknown_health_receives_half_factor() -> None:
    ranked = score_candidates(
        "weighted_random",
        [candidate(1, "unknown", healthy=None), candidate(2, "healthy", healthy=True)],
    )
    unknown = next(item for item in ranked if item.backend_id == 1)
    healthy = next(item for item in ranked if item.backend_id == 2)
    assert unknown.excluded is False
    assert unknown.score == pytest.approx(healthy.score * 0.5)


def test_round_robin_ignores_latency() -> None:
    ranked = score_candidates(
        "round_robin",
        [candidate(1, "fast", latency_ms=10), candidate(2, "slow", latency_ms=1900)],
    )
    assert ranked[0].score == pytest.approx(ranked[1].score)
    assert [item.backend_id for item in ranked] == [1, 2]


def test_least_latency_prefers_lower_latency() -> None:
    ranked = score_candidates(
        "least_latency",
        [candidate(1, "snappy", latency_ms=200), candidate(2, "sluggish", latency_ms=1600)],
    )
    assert [item.backend_id for item in ranked] == [1, 2]
    assert ranked[0].score == pytest.approx(0.94**3, rel=1e-6)
    assert ranked[1].score == pytest.approx(0.52**3, rel=1e-6)


def test_cost_factor_never_exceeds_one() -> None:
    ranked = score_candidates(
        "weighted_random",
        [candidate(1, "free", cost="0"), candidate(2, "cheap", cost="0.000001")],
    )
    free = next(item for item in ranked if item.backend_id == 1)
    cheap = next(item for item in ranked if item.backend_id == 2)
    assert free.score == pytest.approx(0.5)
    assert cheap.score == pytest.approx(0.5)


def test_reasons_reflect_active_factors() -> None:
    ranked = score_candidates("round_robin", [candidate(1, "b1", latency_ms=100)])
    assert ranked[0].reasons == ["health_ok"]
    ranked_latency = score_candidates("least_latency", [candidate(1, "b1", latency_ms=100)])
    assert ranked_latency[0].reasons == ["health_ok", "latency_measured"]


def test_snapshot_is_json_serializable() -> None:
    ranked = score_candidates("weighted_random", [candidate(1, "b1", weight="1.5")])
    payload = json.dumps(ranked[0].snapshot())
    restored = json.loads(payload)
    assert restored["backend_id"] == 1
    assert restored["weight"] == "1.5"
    assert restored["score"] == pytest.approx(0.75)


def test_pick_by_weight_walks_cumulative_scores_from_top() -> None:
    ranked = score_candidates(
        "weighted_random",
        [candidate(1, "low", weight="1"), candidate(2, "high", weight="3")],
    )
    assert [item.name for item in ranked] == ["high", "low"]
    assert pick_by_weight(ranked, 0.0).backend_id == 2
    assert pick_by_weight(ranked, 0.7).backend_id == 2
    assert pick_by_weight(ranked, 0.8).backend_id == 1
    assert pick_by_weight(ranked, 1.0).backend_id == 1


def test_pick_by_weight_distribution_matches_weights() -> None:
    ranked = score_candidates(
        "weighted_random",
        [candidate(1, "low", weight="1"), candidate(2, "high", weight="3")],
    )
    counts = {1: 0, 2: 0}
    steps = 10_000
    for step in range(steps):
        picked = pick_by_weight(ranked, step / steps)
        counts[picked.backend_id] += 1
    assert counts[2] == pytest.approx(steps * 0.75, abs=steps * 0.005)
    assert counts[1] == pytest.approx(steps * 0.25, abs=steps * 0.005)


def test_pick_by_weight_returns_none_when_everything_excluded() -> None:
    ranked = score_candidates("weighted_random", [candidate(1, "sick", healthy=False)])
    assert pick_by_weight(ranked, 0.5) is None


def test_pick_by_weight_clamps_out_of_range_fraction() -> None:
    ranked = score_candidates(
        "weighted_random",
        [candidate(1, "low", weight="1"), candidate(2, "high", weight="3")],
    )
    assert pick_by_weight(ranked, -5.0).backend_id == 2
    assert pick_by_weight(ranked, 42.0).backend_id == 1


def test_candidate_score_is_frozen() -> None:
    ranked = score_candidates("weighted_random", [candidate(1, "b1")])
    with pytest.raises(Exception):
        ranked[0].score = 99.0  # type: ignore[misc]
    assert isinstance(ranked[0], CandidateScore)
