from dataclasses import dataclass, field
from decimal import Decimal

POLICY_FACTORS: dict[str, dict[str, float]] = {
    "weighted_random": {"health": 1.0, "latency": 1.0, "cost": 1.0},
    "least_latency": {"health": 1.0, "latency": 3.0, "cost": 0.0},
    "round_robin": {"health": 1.0, "latency": 0.0, "cost": 0.0},
    "session_sticky": {"health": 1.0, "latency": 0.5, "cost": 0.5},
}

HEALTH_FACTOR = {"healthy": 1.0, "unhealthy": 0.0, "unknown": 0.5}

LATENCY_BASELINE_MS = 2000.0

COST_BASELINE = 0.000005


@dataclass(frozen=True)
class CandidateScore:
    backend_id: int
    name: str
    weight: Decimal
    healthy: bool | None
    latency_ms: int | None
    cost_per_token: Decimal
    score: float
    excluded: bool
    reasons: list[str] = field(default_factory=list)

    def snapshot(self) -> dict[str, object]:
        return {
            "backend_id": self.backend_id,
            "name": self.name,
            "weight": str(self.weight),
            "healthy": self.healthy,
            "latency_ms": self.latency_ms,
            "cost_per_token": str(self.cost_per_token),
            "score": round(self.score, 6),
            "excluded": self.excluded,
            "reasons": list(self.reasons),
        }


def _health_factor(healthy: bool | None) -> tuple[float, str]:
    if healthy is None:
        return HEALTH_FACTOR["unknown"], "health_unknown"
    if healthy:
        return HEALTH_FACTOR["healthy"], "health_ok"
    return HEALTH_FACTOR["unhealthy"], "health_failed"


def _latency_factor(latency_ms: int | None) -> tuple[float, str]:
    if latency_ms is None:
        return 0.5, "latency_unknown"
    ratio = min(max(latency_ms, 0) / LATENCY_BASELINE_MS, 1.0)
    return round(1.0 - 0.6 * ratio, 6), "latency_measured"


def _cost_factor(cost_per_token: Decimal) -> tuple[float, str]:
    cost = float(cost_per_token)
    if cost <= 0:
        return 1.0, "cost_free"
    return round(min(COST_BASELINE / cost, 1.0), 6), "cost_measured"


def score_candidates(
    policy: str,
    candidates: list[dict[str, object]],
) -> list[CandidateScore]:
    weights = POLICY_FACTORS.get(policy)
    if weights is None:
        raise ValueError(f"unknown policy: {policy}")

    scored: list[CandidateScore] = []
    for item in candidates:
        backend_id = int(item["backend_id"])  # type: ignore[arg-type]
        name = str(item["name"])
        weight = Decimal(str(item["weight"]))
        raw_health = item.get("healthy")
        healthy = raw_health if isinstance(raw_health, bool) else None
        raw_latency = item.get("latency_ms")
        latency_ms = int(raw_latency) if isinstance(raw_latency, int) else None
        cost_per_token = Decimal(str(item.get("cost_per_token", 0)))

        health_value, health_reason = _health_factor(healthy)
        latency_value, latency_reason = _latency_factor(latency_ms)
        cost_value, cost_reason = _cost_factor(cost_per_token)

        score = (
            float(weight)
            * health_value ** weights["health"]
            * latency_value ** weights["latency"]
            * cost_value ** weights["cost"]
        )

        reasons = [health_reason]
        if weights["latency"] > 0:
            reasons.append(latency_reason)
        if weights["cost"] > 0:
            reasons.append(cost_reason)

        scored.append(
            CandidateScore(
                backend_id=backend_id,
                name=name,
                weight=weight,
                healthy=healthy,
                latency_ms=latency_ms,
                cost_per_token=cost_per_token,
                score=round(score, 6),
                excluded=health_value == 0.0,
                reasons=reasons,
            )
        )

    return sorted(scored, key=lambda candidate: (-candidate.score, candidate.backend_id))


def pick_by_weight(ranked: list[CandidateScore], fraction: float) -> CandidateScore | None:
    eligible = [candidate for candidate in ranked if not candidate.excluded]
    if not eligible:
        return None
    total = sum(candidate.score for candidate in eligible)
    if total <= 0:
        return eligible[0]
    cursor = min(max(fraction, 0.0), 1.0) * total
    running = 0.0
    for candidate in eligible:
        running += candidate.score
        if cursor <= running:
            return candidate
    return eligible[-1]
