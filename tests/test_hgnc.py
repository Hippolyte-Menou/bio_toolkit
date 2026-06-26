"""Unit tests for bio_toolkit.clients.hgnc. HTTP mocked, no network."""

import pytest

from bio_toolkit.clients import hgnc


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


PAX6_DOC = {
    "response": {
        "docs": [
            {
                "symbol": "PAX6",
                "name": "paired box 6",
                "alias_symbol": ["AN2", "MGDA"],
                "prev_symbol": ["AN", "D11S812E"],
                "hgnc_id": "HGNC:8620",
                "entrez_id": "5080",
                "ensembl_gene_id": "ENSG00000007372",
                "refseq_accession": ["NM_000280"],
                "location": "11p13",
                "uniprot_ids": ["P26367"],
            }
        ]
    }
}

EMPTY_DOC = {"response": {"docs": []}}


def _patch_get(monkeypatch, payload, status_code=200):
    def fake_get(url, headers=None, timeout=None):
        return FakeResponse(payload, status_code)

    monkeypatch.setattr(hgnc.requests, "get", fake_get)


def test_fetch_hgnc_record_parses_all_fields(monkeypatch):
    _patch_get(monkeypatch, PAX6_DOC)
    record = hgnc.fetch_hgnc_record("PAX6")

    assert record["symbol"] == "PAX6"
    assert record["name"] == "paired box 6"
    assert record["alias_symbol"] == ["AN2", "MGDA"]
    assert record["prev_symbol"] == ["AN", "D11S812E"]
    assert record["hgnc_id"] == "HGNC:8620"
    assert record["entrez_id"] == "5080"
    assert record["ensembl_gene_id"] == "ENSG00000007372"
    assert record["refseq_accession"] == ["NM_000280"]
    assert record["location"] == "11p13"
    assert record["uniprot_ids"] == ["P26367"]


def test_fetch_hgnc_record_missing_entry_returns_none(monkeypatch):
    _patch_get(monkeypatch, EMPTY_DOC)
    assert hgnc.fetch_hgnc_record("NOTAGENE") is None


def test_fetch_hgnc_record_normalises_missing_list_fields(monkeypatch):
    # HGNC omits keys entirely when empty; client must coerce to lists/"".
    sparse = {"response": {"docs": [{"symbol": "FOO", "name": "foo gene"}]}}
    _patch_get(monkeypatch, sparse)
    record = hgnc.fetch_hgnc_record("FOO")

    assert record["alias_symbol"] == []
    assert record["prev_symbol"] == []
    assert record["refseq_accession"] == []
    assert record["uniprot_ids"] == []
    assert record["hgnc_id"] == ""


def test_get_gene_aliases_flattens_record(monkeypatch):
    _patch_get(monkeypatch, PAX6_DOC)
    aliases = hgnc.get_gene_aliases("PAX6")

    assert aliases == {"PAX6", "AN2", "MGDA", "AN", "D11S812E", "paired box 6"}


def test_get_gene_aliases_missing_entry_returns_symbol_itself(monkeypatch):
    # Preserve the bot contract: missing -> {symbol}, not empty set.
    _patch_get(monkeypatch, EMPTY_DOC)
    assert hgnc.get_gene_aliases("NOTAGENE") == {"NOTAGENE"}


def test_fetch_hgnc_record_retries_then_succeeds(monkeypatch):
    import requests

    calls = {"n": 0}

    def flaky_get(url, headers=None, timeout=None):
        calls["n"] += 1
        if calls["n"] < 2:
            raise requests.exceptions.ConnectionError("boom")
        return FakeResponse(PAX6_DOC)

    monkeypatch.setattr(hgnc.requests, "get", flaky_get)
    # base_delay in _fetch_symbol is 1.0; patch sleep so the test is fast.
    monkeypatch.setattr("bio_toolkit.util.retry.time.sleep", lambda s: None)

    record = hgnc.fetch_hgnc_record("PAX6")
    assert record["symbol"] == "PAX6"
    assert calls["n"] == 2
