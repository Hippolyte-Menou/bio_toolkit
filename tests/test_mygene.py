"""Unit tests for bio_toolkit.clients.mygene. HTTP mocked, no network."""

from unittest.mock import patch

import pytest
import requests

from bio_toolkit.clients import mygene


class FakeResponse:
    def __init__(self, payload, status_code=200, url="https://mygene.info/v3/query"):
        self._payload = payload
        self.status_code = status_code
        self.url = url

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")


SAMPLE_HIT = {
    "hits": [
        {
            "symbol": "PAX6",
            "name": "paired box 6",
            "alias": ["AN2", "MGDA"],
            "MIM": "607108",
            "Ensembl": {"gene": "ENSG00000007372"},
            "go": {"BP": [{"id": "GO:0001654", "term": "eye development"}]},
        }
    ],
    "total": 1,
}


def test_fetch_mygene_returns_parsed_json():
    captured = {}

    def fake_get(url, params=None, headers=None, timeout=None):
        captured["url"] = url
        captured["params"] = params
        return FakeResponse(SAMPLE_HIT)

    with patch.object(mygene.requests, "get", side_effect=fake_get):
        data = mygene.fetch_mygene("PAX6")

    assert data == SAMPLE_HIT
    assert captured["url"] == mygene.MYGENE_QUERY_URL
    assert captured["params"]["q"] == "symbol:PAX6"
    assert captured["params"]["species"] == "human"
    # default field projection is forwarded verbatim
    assert captured["params"]["fields"] == mygene.DEFAULT_FIELDS


def test_parse_gene_info_extracts_ensembl_and_aliases():
    info = mygene.parse_gene_info(SAMPLE_HIT)
    assert info["ensembl"] == "ENSG00000007372"
    assert info["aliases"] == ["AN2", "MGDA"]


def test_parse_gene_info_ensembl_as_list():
    payload = {
        "hits": [
            {
                "symbol": "X",
                "Ensembl": [{"gene": "ENSG00000000001"}, {"gene": "LRG_1"}],
                "alias": "SOLO",
            }
        ]
    }
    info = mygene.parse_gene_info(payload)
    assert info["ensembl"] == "ENSG00000000001"
    # a single string alias is normalised to a list
    assert info["aliases"] == ["SOLO"]


def test_parse_gene_info_no_hits():
    info = mygene.parse_gene_info({"hits": []})
    assert info == {"ensembl": None, "aliases": []}


def test_fetch_mygene_raises_on_http_error():
    # 404 is not in the retry-on-status set, so raise_for_status surfaces it
    # immediately without the decorator sleeping/retrying.
    def fake_get(url, params=None, headers=None, timeout=None):
        return FakeResponse({}, status_code=404)

    with patch.object(mygene.requests, "get", side_effect=fake_get):
        with pytest.raises(requests.HTTPError):
            mygene.fetch_mygene("PAX6")
