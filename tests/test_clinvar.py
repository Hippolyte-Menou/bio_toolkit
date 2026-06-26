"""Unit tests for bio_toolkit.clients.clinvar. HTTP mocked, no network."""

from unittest.mock import patch

import pytest
import requests

from bio_toolkit.clients import clinvar


class FakeResponse:
    def __init__(self, payload, status_code=200, url="https://eutils.ncbi.nlm.nih.gov"):
        self._payload = payload
        self.status_code = status_code
        self.url = url

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")


ESEARCH_HITS = {
    "esearchresult": {"count": "2", "idlist": ["111", "222"]},
}
ESEARCH_EMPTY = {
    "esearchresult": {"count": "0", "idlist": []},
}
ESUMMARY = {
    "result": {
        "uids": ["111", "222"],
        "111": {"title": "PAX6 variant", "germline_classification": {}},
        "222": {"title": "Another variant", "germline_classification": {}},
    }
}


def _router(esearch_payload, esummary_payload=None):
    """Return a fake requests.get that routes by URL (esearch vs esummary)."""
    calls = []

    def fake_get(url, params=None, headers=None, timeout=None):
        calls.append((url, params))
        if "esearch" in url:
            return FakeResponse(esearch_payload)
        if "esummary" in url:
            return FakeResponse(esummary_payload or {})
        raise AssertionError(f"unexpected url {url}")

    return fake_get, calls


def test_default_term_is_gene_only_no_phenotype_filter():
    fake_get, calls = _router(ESEARCH_EMPTY)
    with patch.object(clinvar.requests, "get", side_effect=fake_get):
        clinvar.fetch_clinvar("PAX6")

    esearch_call = next(c for c in calls if "esearch" in c[0])
    term = esearch_call[1]["term"]
    # The MAC ocular-phenotype clause must NOT be present in the shared client.
    assert term == "PAX6[gene]"
    assert "disease/phenotype" not in term
    assert "coloboma" not in term.lower()


def test_fetch_returns_combined_esearch_and_esummary():
    fake_get, calls = _router(ESEARCH_HITS, ESUMMARY)
    # patch sleep so the NCBI pacing doesn't slow the test
    with patch.object(clinvar.time, "sleep"), \
            patch.object(clinvar.requests, "get", side_effect=fake_get):
        out = clinvar.fetch_clinvar("PAX6")

    assert out["esearch"] == ESEARCH_HITS
    assert out["esummary"] == ESUMMARY
    # both endpoints hit
    assert any("esearch" in u for u, _ in calls)
    assert any("esummary" in u for u, _ in calls)
    # esummary received the joined idlist
    esum_call = next(c for c in calls if "esummary" in c[0])
    assert esum_call[1]["id"] == "111,222"


def test_no_ids_skips_esummary():
    fake_get, calls = _router(ESEARCH_EMPTY)
    with patch.object(clinvar.time, "sleep"), \
            patch.object(clinvar.requests, "get", side_effect=fake_get):
        out = clinvar.fetch_clinvar("PAX6")

    assert out["esearch"] == ESEARCH_EMPTY
    assert out["esummary"] is None
    assert not any("esummary" in u for u, _ in calls)


def test_custom_term_is_forwarded_verbatim():
    fake_get, calls = _router(ESEARCH_EMPTY)
    with patch.object(clinvar.requests, "get", side_effect=fake_get):
        clinvar.fetch_clinvar("PAX6", term="PAX6[gene] AND pathogenic")

    esearch_call = next(c for c in calls if "esearch" in c[0])
    assert esearch_call[1]["term"] == "PAX6[gene] AND pathogenic"


def test_fetch_raises_on_http_error():
    def fake_get(url, params=None, headers=None, timeout=None):
        return FakeResponse({}, status_code=404)

    with patch.object(clinvar.requests, "get", side_effect=fake_get):
        with pytest.raises(requests.HTTPError):
            clinvar.fetch_clinvar("PAX6")
