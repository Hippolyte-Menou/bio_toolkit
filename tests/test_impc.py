"""Unit tests for bio_toolkit.clients.impc. HTTP is mocked; no network."""

import requests

from bio_toolkit.clients import impc


class FakeResponse:
    def __init__(self, *, json_data=None, status_code=200):
        self._json = json_data
        self.status_code = status_code

    def json(self):
        return self._json

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.exceptions.HTTPError(f"status {self.status_code}")


SOLR_DOCS = [
    {
        "marker_symbol": "Pax6",
        "allele_symbol": "Pax6<tm1>",
        "mp_term_id": "MP:0001147",
        "mp_term_name": "small eye",
        "top_level_mp_term_name": ["vision/eye phenotype"],
        "p_value": 1e-9,
        "phenotyping_center": "WTSI",
    },
    {
        "marker_symbol": "Pax6",
        "allele_symbol": "Pax6<tm1>",
        "mp_term_id": "MP:0002092",
        "mp_term_name": "abnormal heart morphology",
        "top_level_mp_term_name": ["cardiovascular system phenotype"],
        "p_value": 2e-5,
        "phenotyping_center": "WTSI",
    },
]

SOLR_RESPONSE = {"response": {"numFound": 2, "docs": SOLR_DOCS}}


def test_fetch_impc_returns_raw_docs_unfiltered(monkeypatch):
    captured = {}

    def fake_get(url, params=None, headers=None, timeout=None):
        captured["url"] = url
        captured["params"] = params
        return FakeResponse(json_data=SOLR_RESPONSE)

    monkeypatch.setattr(impc.requests, "get", fake_get)

    out = impc.fetch_impc("Pax6")

    assert out["gene"] == "Pax6"
    assert out["num_found"] == 2
    # RAW docs: both ocular AND non-ocular rows present (no filtering carried over).
    assert len(out["docs"]) == 2
    names = {d["mp_term_name"] for d in out["docs"]}
    assert names == {"small eye", "abnormal heart morphology"}
    assert out["raw"] == SOLR_RESPONSE

    # Query targeted the marker_symbol Solr field.
    assert captured["params"]["q"] == "marker_symbol:Pax6"
    assert captured["params"]["rows"] == 200
    assert captured["params"]["wt"] == "json"


def test_fetch_impc_respects_rows_arg(monkeypatch):
    captured = {}

    def fake_get(url, params=None, headers=None, timeout=None):
        captured["params"] = params
        return FakeResponse(json_data={"response": {"numFound": 0, "docs": []}})

    monkeypatch.setattr(impc.requests, "get", fake_get)

    out = impc.fetch_impc("FOO", rows=50)
    assert captured["params"]["rows"] == 50
    assert out["docs"] == []
    assert out["num_found"] == 0


def test_fetch_impc_handles_missing_response_key(monkeypatch):
    def fake_get(url, params=None, headers=None, timeout=None):
        return FakeResponse(json_data={})

    monkeypatch.setattr(impc.requests, "get", fake_get)

    out = impc.fetch_impc("BAR")
    assert out["docs"] == []
    assert out["num_found"] == 0
    assert out["raw"] == {}


def test_fetch_impc_retries_on_transient_then_succeeds(monkeypatch):
    calls = {"n": 0}

    def flaky_get(url, params=None, headers=None, timeout=None):
        calls["n"] += 1
        if calls["n"] < 2:
            raise requests.exceptions.ConnectionError("boom")
        return FakeResponse(json_data=SOLR_RESPONSE)

    monkeypatch.setattr(impc.requests, "get", flaky_get)
    # base_delay is 2.0 in the client; patch the retry helper's sleep so the
    # test runs instantly (the decorator lives in bio_toolkit.util.retry).
    from bio_toolkit.util import retry as retry_mod
    monkeypatch.setattr(retry_mod.time, "sleep", lambda *_: None)

    out = impc.fetch_impc("Pax6")
    assert calls["n"] == 2
    assert out["num_found"] == 2
