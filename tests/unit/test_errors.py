import pytest
from evalp import errors as E

TABLE = [
    (E.ValidationFailed, 400, "VALIDATION_ERROR"),
    (E.SpecInvalid, 400, "SPEC_INVALID"),
    (E.GitDirty, 400, "GIT_DIRTY"),
    (E.Unauthorized, 401, "UNAUTHORIZED"),
    (E.NotFound, 404, "NOT_FOUND"),
    (E.DraftConflict, 409, "DRAFT_CONFLICT"),
    (E.SessionBusy, 409, "SESSION_BUSY"),
    (E.AssetInUse, 409, "ASSET_IN_USE"),
    (E.InvalidState, 409, "INVALID_STATE"),
    (E.DatasetParseError, 422, "DATASET_PARSE_ERROR"),
    (E.ProviderError, 502, "PROVIDER_ERROR"),
    (E.AgentError, 502, "AGENT_ERROR"),
    (E.Timeout, 504, "TIMEOUT"),
]


@pytest.mark.parametrize(("cls", "status", "code"), TABLE)
def test_code_and_status(cls, status, code):
    err = cls("出错了", {"k": 1})
    assert isinstance(err, E.EvalpError)
    assert (err.status, err.code, err.message, err.details) == (status, code, "出错了", {"k": 1})
    assert err.to_dict() == {"error": {"code": code, "message": "出错了", "details": {"k": 1}}}


def test_covers_design_table():
    assert {c for _, _, c in TABLE} == {c.default_code for c in E.ERROR_CLASSES}
    assert len(E.ERROR_CLASSES) == 13


def test_defaults_and_override():
    assert E.NotFound().message and E.NotFound().details == {}
    c = E.Conflict("重名", code="NAME_CONFLICT")
    assert (c.status, c.code) == (409, "NAME_CONFLICT")
    base = E.EvalpError("x", code="X", status=418)
    assert base.status == 418


def test_base_signature():
    e = E.EvalpError(code="C", message="m", status=500, details={"a": 1})
    assert (e.code, e.message, e.status, e.details) == ("C", "m", 500, {"a": 1})
