import pytest

from src.api.errors import classify_integrity_error


class FakeAsyncpgError(Exception):
    def __init__(self, sqlstate: str, message: str) -> None:
        super().__init__(message)
        self.sqlstate = sqlstate
        self.message = message


@pytest.mark.parametrize(
    ("sqlstate", "expected_code", "expected_status"),
    [
        ("23505", "unique_violation", 409),
        ("23503", "foreign_key_violation", 422),
        ("23514", "check_violation", 422),
        ("23502", "not_null_violation", 422),
    ],
)
def test_sqlstate_mapping(sqlstate: str, expected_code: str, expected_status: int) -> None:
    origin = FakeAsyncpgError(sqlstate, "some constraint failed")
    status_code, code, message = classify_integrity_error(origin)
    assert (status_code, code) == (expected_status, expected_code)
    assert message == "some constraint failed"


def test_unknown_sqlstate_falls_back_to_conflict() -> None:
    origin = FakeAsyncpgError("99999", "mystery")
    status_code, code, _ = classify_integrity_error(origin)
    assert (status_code, code) == (409, "integrity_violation")


def test_message_is_truncated_to_first_line() -> None:
    origin = FakeAsyncpgError("23505", "duplicate key value violates unique constraint\nDETAIL: ...")
    _, _, message = classify_integrity_error(origin)
    assert message == "duplicate key value violates unique constraint"


def test_origin_without_sqlstate_or_message() -> None:
    status_code, code, message = classify_integrity_error(RuntimeError("boom"))
    assert (status_code, code) == (409, "integrity_violation")
    assert message == "boom"
