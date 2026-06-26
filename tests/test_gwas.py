"""Unit tests for bio_toolkit.clients.gwas. HTTP mocked, no network."""

from unittest.mock import patch

import pytest
import requests

from bio_toolkit.clients import gwas


class FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")


ASSOC_URL = "https://www.ebi.ac.uk/gwas/rest/api/snp/rs1/associations"
TRAIT_URL = "https://www.ebi.ac.uk/gwas/rest/api/assoc/1/efoTraits"

FIND_BY_GENE = {
    "_embedded": {
        "singleNucleotidePolymorphisms": [
            {
                "rsId": "rs1",
                "functionalClass": "intron_variant",
                "_links": {"associations": {"href": ASSOC_URL}},
            }
        ]
    }
}

ASSOCIATIONS = {
    "_embedded": {
        "associations": [
            {"_links": {"efoTraits": {"href": TRAIT_URL}}}
        ]
    }
}

TRAITS = {
    "_embedded": {
        "efoTraits": [
            {"trait": "myopia", "uri": "http://www.ebi.ac.uk/efo/EFO_0003937"}
        ]
    }
}

FIND_BY_GENE_EMPTY = {
    "_embedded": {"singleNucleotidePolymorphisms": []}
}


def _router(by_url):
    """by_url: dict of url-substring -> payload. Returns fake get + call log."""
    calls = []

    def fake_get(url, params=None, headers=None, timeout=None):
        calls.append((url, params))
        for key, payload in by_url.items():
            if key in url:
                return FakeResponse(payload)
        raise AssertionError(f"unexpected url {url}")

    return fake_get, calls


def test_fetch_gwas_full_traversal():
    fake_get, calls = _router({
        "findByGene": FIND_BY_GENE,
        "associations": ASSOCIATIONS,
        "efoTraits": TRAITS,
    })
    with patch.object(gwas.requests, "get", side_effect=fake_get):
        out = gwas.fetch_gwas("PAX6")

    # compact projection preserved (no full HAL payload stored)
    assert out["findByGene_summary"]["total_snps"] == 1
    assert out["findByGene_summary"]["snps"] == [
        {"rsId": "rs1", "functionalClass": "intron_variant"}
    ]
    # association count recorded per SNP
    assert out["associations_per_snp"]["rs1"] == {"n": 1}
    # trait payload captured raw, keyed by efoTrait url
    assert out["traits"][TRAIT_URL] == TRAITS["_embedded"]["efoTraits"]
    # no ocular flagging leaked into the output structure
    assert "ocular" not in out
    assert "findings" not in out


def test_no_snps_returns_empty_summary():
    fake_get, calls = _router({"findByGene": FIND_BY_GENE_EMPTY})
    with patch.object(gwas.requests, "get", side_effect=fake_get):
        out = gwas.fetch_gwas("PAX6")

    assert out["findByGene_summary"]["total_snps"] == 0
    assert out["associations_per_snp"] == {}
    assert out["traits"] == {}
    # only the findByGene call was made
    assert len(calls) == 1


def test_association_subrequest_error_is_captured_not_raised():
    def fake_get(url, params=None, headers=None, timeout=None):
        if "findByGene" in url:
            return FakeResponse(FIND_BY_GENE)
        if "associations" in url:
            raise requests.exceptions.ConnectionError("down")
        raise AssertionError(f"unexpected url {url}")

    # patch retry's sleep so the ConnectionError retries don't slow the test;
    # after retries are exhausted the error is caught by fetch_gwas itself.
    with patch.object(gwas.requests, "get", side_effect=fake_get), \
            patch("bio_toolkit.util.retry.time.sleep"):
        out = gwas.fetch_gwas("PAX6")

    assert "error" in out["associations_per_snp"]["rs1"]
    assert out["traits"] == {}


def test_findbygene_http_error_propagates():
    def fake_get(url, params=None, headers=None, timeout=None):
        return FakeResponse({}, status_code=404)

    with patch.object(gwas.requests, "get", side_effect=fake_get):
        with pytest.raises(requests.HTTPError):
            gwas.fetch_gwas("PAX6")
