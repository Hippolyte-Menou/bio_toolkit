"""Unit tests for bio_toolkit.clients.ncbi_gene. HTTP mocked, no network."""

import pytest

from bio_toolkit.clients import ncbi_gene


class FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            import requests
            raise requests.exceptions.HTTPError(f"HTTP {self.status_code}")


ESEARCH_PAX6 = {"esearchresult": {"idlist": ["5080"]}}
ESEARCH_EMPTY = {"esearchresult": {"idlist": []}}

ESUMMARY_PAX6 = {
    "result": {
        "5080": {
            "name": "PAX6",
            "description": "paired box 6",
            "otheraliases": "AN2, MGDA, WAGR",
            "chromosome": "11",
            "maplocation": "11p13",
            "summary": "This gene encodes a transcription factor...",
        }
    }
}
ESUMMARY_MISSING = {"result": {}}


def test_gene_url():
    assert ncbi_gene.gene_url("5080") == "https://www.ncbi.nlm.nih.gov/gene/5080"
    assert ncbi_gene.gene_url(5080) == "https://www.ncbi.nlm.nih.gov/gene/5080"


def test_esearch_gene_id_found(monkeypatch):
    monkeypatch.setattr(
        ncbi_gene, "_get", lambda url, params: FakeResponse(ESEARCH_PAX6)
    )
    assert ncbi_gene.esearch_gene_id("PAX6") == "5080"


def test_esearch_gene_id_not_found(monkeypatch):
    monkeypatch.setattr(
        ncbi_gene, "_get", lambda url, params: FakeResponse(ESEARCH_EMPTY)
    )
    assert ncbi_gene.esearch_gene_id("NOTAGENE") is None


def test_esearch_builds_correct_term(monkeypatch):
    captured = {}

    def fake_get(url, params):
        captured["params"] = params
        return FakeResponse(ESEARCH_PAX6)

    monkeypatch.setattr(ncbi_gene, "_get", fake_get)
    ncbi_gene.esearch_gene_id("PAX6")
    assert captured["params"]["term"] == "PAX6[sym] AND Homo sapiens[orgn]"
    assert captured["params"]["db"] == "gene"


def test_esummary_gene_parses_fields(monkeypatch):
    monkeypatch.setattr(
        ncbi_gene, "_get", lambda url, params: FakeResponse(ESUMMARY_PAX6)
    )
    summary = ncbi_gene.esummary_gene("5080")

    assert summary["gene_id"] == "5080"
    assert summary["symbol"] == "PAX6"
    assert summary["name"] == "paired box 6"
    assert summary["description"] == "paired box 6"
    assert summary["aliases"] == ["AN2", "MGDA", "WAGR"]
    assert summary["chromosome"] == "11"
    assert summary["map_location"] == "11p13"
    assert summary["summary"].startswith("This gene encodes")
    assert summary["url"] == "https://www.ncbi.nlm.nih.gov/gene/5080"


def test_esummary_gene_missing_doc_returns_none(monkeypatch):
    monkeypatch.setattr(
        ncbi_gene, "_get", lambda url, params: FakeResponse(ESUMMARY_MISSING)
    )
    assert ncbi_gene.esummary_gene("9999") is None


def test_esummary_gene_handles_empty_aliases(monkeypatch):
    payload = {"result": {"5080": {"name": "PAX6", "description": "paired box 6"}}}
    monkeypatch.setattr(ncbi_gene, "_get", lambda url, params: FakeResponse(payload))
    summary = ncbi_gene.esummary_gene("5080")
    assert summary["aliases"] == []
    assert summary["chromosome"] == ""


def test_fetch_gene_end_to_end(monkeypatch):
    def fake_get(url, params):
        if url == ncbi_gene.ESEARCH_URL:
            return FakeResponse(ESEARCH_PAX6)
        return FakeResponse(ESUMMARY_PAX6)

    monkeypatch.setattr(ncbi_gene, "_get", fake_get)
    result = ncbi_gene.fetch_gene("PAX6")
    assert result["gene_id"] == "5080"
    assert result["symbol"] == "PAX6"
    assert result["aliases"] == ["AN2", "MGDA", "WAGR"]


def test_fetch_gene_unresolved_symbol_returns_none(monkeypatch):
    monkeypatch.setattr(
        ncbi_gene, "_get", lambda url, params: FakeResponse(ESEARCH_EMPTY)
    )
    assert ncbi_gene.fetch_gene("NOTAGENE") is None


def test_fetch_gene_retries_then_succeeds(monkeypatch):
    import requests

    calls = {"n": 0}

    def flaky_get(url, params=None, timeout=None):
        calls["n"] += 1
        if calls["n"] < 2:
            raise requests.exceptions.Timeout("slow")
        if url == ncbi_gene.ESEARCH_URL:
            return FakeResponse(ESEARCH_PAX6)
        return FakeResponse(ESUMMARY_PAX6)

    monkeypatch.setattr(ncbi_gene.requests, "get", flaky_get)
    monkeypatch.setattr("bio_toolkit.util.retry.time.sleep", lambda s: None)

    result = ncbi_gene.fetch_gene("PAX6")
    assert result["symbol"] == "PAX6"
