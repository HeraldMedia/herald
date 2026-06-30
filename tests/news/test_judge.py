from types import SimpleNamespace

from herald.validator.news import judge as judgemod
from herald.validator.news.judge import judge


def fake_client(answer):
    return SimpleNamespace(
        BRIEF_EVALUATION_MODEL="pinned-model",
        _make_request=lambda model, messages, temperature: {
            "choices": [{"message": {"content": answer}}]
        },
    )


def test_yes_is_true(monkeypatch):
    monkeypatch.setattr(judgemod, "_get_client", lambda: fake_client("Yes, clearly."))
    assert judge("paid?", "text") is True


def test_no_is_false(monkeypatch):
    monkeypatch.setattr(judgemod, "_get_client", lambda: fake_client("No."))
    assert judge("paid?", "text") is False


def test_ambiguous_is_none(monkeypatch):
    monkeypatch.setattr(judgemod, "_get_client", lambda: fake_client("maybe"))
    assert judge("paid?", "text") is None


def test_error_is_none(monkeypatch):
    def boom():
        raise RuntimeError("api down")
    monkeypatch.setattr(judgemod, "_get_client", boom)
    assert judge("paid?", "text") is None


def test_temperature_zero_and_pinned_model(monkeypatch):
    seen = {}

    def client():
        def make(model, messages, temperature):
            seen["model"] = model
            seen["temperature"] = temperature
            return {"choices": [{"message": {"content": "no"}}]}
        return SimpleNamespace(BRIEF_EVALUATION_MODEL="pinned-model", _make_request=make)

    monkeypatch.setattr(judgemod, "_get_client", client)
    judge("q", "t")
    assert seen["temperature"] == 0 and seen["model"] == "pinned-model"
