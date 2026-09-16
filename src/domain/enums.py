from enum import StrEnum


class BackendEngine(StrEnum):
    OLLAMA = "ollama"
    OPENAI = "openai"


class BackendState(StrEnum):
    ACTIVE = "active"
    DRAINING = "draining"
    DISABLED = "disabled"


class RequestStatus(StrEnum):
    SUCCESS = "success"
    ERROR = "error"
    TIMEOUT = "timeout"
    REJECTED = "rejected"


class RoutingPolicy(StrEnum):
    WEIGHTED_RANDOM = "weighted_random"
    LEAST_LATENCY = "least_latency"
    ROUND_ROBIN = "round_robin"
    SESSION_STICKY = "session_sticky"
