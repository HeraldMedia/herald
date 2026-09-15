import json
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest

from herald.validator.news import submissions
from herald.validator.news.submissions import fetch_submissions, select_new, validate_rows
from herald.validator.news.url import article_id
from herald.validator.news.vesting import VestingLedger

FIXTURE = Path(__file__).parent / "fixtures" / "submission_row.json"
NETWORK, NETUID = "finney", 69
STORY = "https://www.example.com/news/story"
NOW_TS = datetime(2026, 9, 12, 12, 0, tzinfo=timezone.utc).timestamp()
UPLOADED = int(datetime(2026, 9, 9, 15, 0, tzinfo=timezone.utc).timestamp())
DRAFT = ("A Bittensor subnet opened its public pilot to PR firms and journalists this week. Contributors "
         "upload the text they plan to publish, then add the link once the article is live. Validators "
         "fetch each article, confirm the outlet and publication date, and check that the uploaded text "
         "appears in the published story before any reward begins to vest.")


def row(submission_id="sub-1", url=STORY, **over):
    fields = {"submission_id": submission_id, "network": NETWORK, "netuid": NETUID,
              "brief_id": "brief-1", "url": url, "draft_text": DRAFT, "uploaded_ts": UPLOADED}
    fields.update(over)
    return fields


def kept_ids(rows):
    return [r["submission_id"] for r in validate_rows(rows, NETWORK, NETUID, NOW_TS)]


def selected_ids(selected):
    return [r["submission_id"] for _aid, r in selected]


def test_fixture_row_has_exactly_the_feed_keys_and_validates():
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    assert set(data) == {"submission_id", "network", "netuid", "brief_id", "url", "draft_text", "uploaded_ts"}
    assert set(data) == set(submissions.ROW_KEYS)
    assert validate_rows([data], NETWORK, NETUID, NOW_TS) == [data]


def test_rows_for_another_network_or_netuid_are_dropped():
    rows = [row("keep"), row("other-netuid", netuid=70), row("netuid-string", netuid="69"),
            row("other-network", network="test")]
    assert kept_ids(rows) == ["keep"]


@pytest.mark.parametrize("bad", [
    {"submission_id": ""},
    {"submission_id": "sub 1"},
    {"submission_id": "sub/1"},
    {"submission_id": "s" * 129},
    {"submission_id": 7},
    {"brief_id": ""},
    {"brief_id": None},
    {"brief_id": "b" * 129},
    {"url": None},
    {"url": "http://www.example.com/news/story"},
    {"url": "https:///news/story"},
    {"url": "https://www.exämple.com/news/story"},
    {"url": "https://www.example.com/news/a story"},
    {"url": "https://www.example.com:99999/news/story"},
    {"url": "https://www.example.com/" + "a" * 2048},
])
def test_rows_with_a_bad_id_or_url_are_dropped(bad):
    assert validate_rows([row(**bad)], NETWORK, NETUID, NOW_TS) == []


def test_id_and_url_length_limits_are_inclusive():
    prefix = "https://www.example.com/"
    good = row(submission_id="S" * 128, brief_id="brief:2026.09_a-1", url=prefix + "a" * (2048 - len(prefix)))
    assert validate_rows([good], NETWORK, NETUID, NOW_TS) == [good]


def test_non_object_rows_are_dropped_and_extra_keys_are_not_kept():
    rows = [None, "row", ["sub-1"], {**row(), "payout_address": "5Fexample"}]
    assert validate_rows(rows, NETWORK, NETUID, NOW_TS) == [row()]


@pytest.mark.parametrize("bad", [
    {"draft_text": None},
    {"draft_text": 12345},
    {"draft_text": [DRAFT]},
    {"draft_text": ""},
    {"draft_text": "x" * 299},
    {"draft_text": "  \n" + "x" * 299 + "\t "},
    {"draft_text": " " * 400},
    {"draft_text": "x" * 40_001},
    {"uploaded_ts": None},
    {"uploaded_ts": str(UPLOADED)},
    {"uploaded_ts": float(UPLOADED)},
    {"uploaded_ts": True},
    {"uploaded_ts": 0},
    {"uploaded_ts": -UPLOADED},
    {"uploaded_ts": int(NOW_TS) + 1},
], ids=["draft-none", "draft-int", "draft-list", "draft-empty", "draft-299", "draft-299-padded",
        "draft-blank", "draft-40001", "uploaded-none", "uploaded-string", "uploaded-float",
        "uploaded-bool", "uploaded-zero", "uploaded-negative", "uploaded-after-chain-time"])
def test_rows_with_a_bad_draft_or_upload_time_are_dropped(bad):
    assert validate_rows([row(**bad)], NETWORK, NETUID, NOW_TS) == []


@pytest.mark.parametrize("missing", ["draft_text", "uploaded_ts"])
def test_rows_without_a_draft_or_upload_time_are_dropped(missing):
    incomplete = row()
    del incomplete[missing]
    assert validate_rows([incomplete, row("sub-2", url=STORY + "-2")], NETWORK, NETUID, NOW_TS) == [
        row("sub-2", url=STORY + "-2")]


def test_draft_length_limits_are_inclusive_after_stripping_and_upload_at_chain_time_is_kept():
    rows = [
        row("shortest", url=STORY + "-1", draft_text="\n  " + "x" * 300 + "  \n"),
        row("longest", url=STORY + "-2", draft_text="x" * 40_000),
        row("at-chain-time", url=STORY + "-3", uploaded_ts=int(NOW_TS)),
    ]
    assert validate_rows(rows, NETWORK, NETUID, NOW_TS) == rows


def test_url_whose_canonical_form_keeps_a_query_is_dropped():
    assert validate_rows([row(url=STORY + "?output=amp")], NETWORK, NETUID, NOW_TS) == []


def test_url_with_only_tracking_parameters_is_kept():
    tracked = row(url=STORY + "?utm_source=newsletter&utm_medium=email")
    assert validate_rows([tracked], NETWORK, NETUID, NOW_TS) == [tracked]


def test_only_the_first_feed_rows_are_read(monkeypatch):
    monkeypatch.setattr(submissions, "MAX_FEED_ROWS", 2)
    rows = [row(f"sub-{i}", url=f"{STORY}-{i}") for i in range(3)]
    assert kept_ids(rows) == ["sub-0", "sub-1"]


@pytest.mark.parametrize("reverse", [False, True])
def test_duplicate_articles_keep_the_lowest_submission_id(reverse):
    rows = [row("sub-2", url=STORY + "?utm_source=feed"), row("sub-1", url=STORY + "/")]
    if reverse:
        rows.reverse()
    selected = select_new(rows, VestingLedger(vest_epochs=30), limit=10)
    assert selected_ids(selected) == ["sub-1"]
    assert selected[0][0] == article_id(STORY)


def test_articles_already_in_the_ledger_are_skipped_in_any_status():
    urls = {status: f"{STORY}-{status}" for status in ("vesting", "completed", "clawback", "expired")}
    ledger = VestingLedger(vest_epochs=1)
    for url in urls.values():
        ledger.start(article_id(url), uid=2, total_usd=10.0, start_epoch=1)
    ledger.release(article_id(urls["completed"]), epoch=1)
    ledger.clawback(article_id(urls["clawback"]))
    ledger.expire(article_id(urls["expired"]))
    assert {ledger.status(article_id(u)) for u in urls.values()} == {
        "VESTING", "COMPLETED", "CLAWBACK", "EXPIRED"}

    rows = [row(f"sub-{status}", url=url) for status, url in urls.items()]
    rows.append(row("sub-new", url=f"{STORY}-new"))
    assert selected_ids(select_new(rows, ledger, limit=10)) == ["sub-new"]


def test_limit_applies_after_known_articles_are_skipped():
    rows = [row(f"sub-{i}", url=f"{STORY}-{i}") for i in range(4)]
    ordered = sorted(rows, key=lambda r: article_id(r["url"]))
    ledger = VestingLedger(vest_epochs=30)
    ledger.start(article_id(ordered[0]["url"]), uid=2, total_usd=10.0)
    selected = select_new(rows, ledger, limit=2)
    assert selected_ids(selected) == [ordered[1]["submission_id"], ordered[2]["submission_id"]]


def test_default_limit_is_the_per_epoch_cap(monkeypatch):
    monkeypatch.setattr(submissions, "HERALD_MAX_SUBMISSIONS_PER_EPOCH", 1)
    rows = [row(f"sub-{i}", url=f"{STORY}-{i}") for i in range(3)]
    assert len(select_new(rows, VestingLedger(vest_epochs=30))) == 1


class FakeResponse:
    def __init__(self, status=200, body=None, raw=None):
        self.status_code = status
        self.body = body
        self.raw = raw

    def raise_for_status(self):
        if self.status_code >= 400:
            request = httpx.Request("GET", "http://results.invalid" + submissions.FEED_PATH)
            raise httpx.HTTPStatusError("feed error", request=request,
                                        response=httpx.Response(self.status_code, request=request))

    def json(self):
        return json.loads(self.raw) if self.raw is not None else self.body


def test_fetch_reads_the_feed_for_this_network_with_the_read_credential(monkeypatch):
    calls = []
    monkeypatch.delenv("HERALD_RESULTS_TOKEN", raising=False)
    monkeypatch.setenv("HERALD_RESULTS_READ_TOKEN", "read-token")
    monkeypatch.setattr(submissions.httpx, "get",
                        lambda url, **kwargs: calls.append((url, kwargs)) or FakeResponse(body=[row()]))

    assert fetch_submissions("http://results.invalid/", NETWORK, NETUID) == [row()]
    [(url, kwargs)] = calls
    assert url == "http://results.invalid/api/v4/validator/submissions"
    assert kwargs["params"] == {"network": "finney", "netuid": 69}
    assert kwargs["headers"] == {"X-Results-Token": "read-token"}
    assert kwargs["timeout"] == 10.0


@pytest.mark.parametrize("response", [
    FakeResponse(status=503),
    FakeResponse(status=401),
    FakeResponse(raw="{not json"),
    FakeResponse(body={"rows": []}),
    FakeResponse(body="rows"),
    FakeResponse(body=None),
], ids=["http-503", "http-401", "bad-json", "object-body", "string-body", "null-body"])
def test_unreadable_feed_returns_none(monkeypatch, response):
    monkeypatch.setattr(submissions.httpx, "get", lambda url, **kwargs: response)
    assert fetch_submissions("http://results.invalid", NETWORK, NETUID) is None


def test_connection_failure_returns_none(monkeypatch):
    def refuse(url, **kwargs):
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(submissions.httpx, "get", refuse)
    assert fetch_submissions("http://results.invalid", NETWORK, NETUID) is None


def test_unset_endpoint_returns_none_without_a_request(monkeypatch):
    def unexpected(url, **kwargs):
        raise AssertionError("no request expected")

    monkeypatch.setattr(submissions.httpx, "get", unexpected)
    assert fetch_submissions("", NETWORK, NETUID) is None
    assert fetch_submissions(None, NETWORK, NETUID) is None
