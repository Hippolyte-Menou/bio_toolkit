"""Unit tests for bio_toolkit.clients.pubmed. HTTP mocked, no network."""

from unittest.mock import patch

import pytest
import requests

from bio_toolkit.clients import pubmed


class FakeResponse:
    def __init__(self, payload=None, text="", status_code=200,
                 url="https://eutils.ncbi.nlm.nih.gov"):
        self._payload = payload
        self.text = text
        self.status_code = status_code
        self.url = url

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")


ESEARCH_HITS = {"esearchresult": {"count": "2", "idlist": ["111", "222"]}}
ESEARCH_EMPTY = {"esearchresult": {"count": "0", "idlist": []}}
ESUMMARY = {"result": {"uids": ["111"], "111": {"title": "BMP7 and coloboma",
                                                 "fulljournalname": "Dev Biol",
                                                 "pubdate": "2019 Jan"}}}
EFETCH_XML = """<?xml version="1.0"?>
<PubmedArticleSet>
  <PubmedArticle>
    <MedlineCitation>
      <PMID Version="1">111</PMID>
      <Article>
        <Abstract>
          <AbstractText Label="BACKGROUND">Optic fissure closure.</AbstractText>
          <AbstractText Label="RESULTS">bmp7 morphants show coloboma.</AbstractText>
        </Abstract>
        <PublicationTypeList>
          <PublicationType UI="D016428">Journal Article</PublicationType>
        </PublicationTypeList>
      </Article>
      <MeshHeadingList>
        <MeshHeading><DescriptorName UI="D">Coloboma</DescriptorName></MeshHeading>
        <MeshHeading><DescriptorName UI="D">Zebrafish</DescriptorName></MeshHeading>
      </MeshHeadingList>
    </MedlineCitation>
  </PubmedArticle>
</PubmedArticleSet>"""


def _router(esearch=None, esummary=None, efetch_text=""):
    calls = []

    def fake_get(url, params=None, headers=None, timeout=None):
        calls.append((url, params))
        if "esearch" in url:
            return FakeResponse(payload=esearch)
        if "esummary" in url:
            return FakeResponse(payload=esummary)
        if "efetch" in url:
            return FakeResponse(text=efetch_text)
        raise AssertionError(f"unexpected url {url}")

    return fake_get, calls


def test_esearch_parses_count_and_idlist():
    fake_get, _ = _router(esearch=ESEARCH_HITS)
    with patch.object(pubmed.requests, "get", side_effect=fake_get):
        out = pubmed.esearch("BMP7 AND coloboma", retmax=40)
    assert out["count"] == "2"
    assert out["idlist"] == ["111", "222"]


def test_efetch_extracts_abstract_mesh_pubtypes():
    fake_get, _ = _router(efetch_text=EFETCH_XML)
    with patch.object(pubmed.requests, "get", side_effect=fake_get):
        out = pubmed.efetch(["111"])
    rec = out["111"]
    assert "Optic fissure closure." in rec["abstract"]
    assert "bmp7 morphants show coloboma." in rec["abstract"]
    assert "Coloboma" in rec["mesh"] and "Zebrafish" in rec["mesh"]
    assert "Journal Article" in rec["pubtypes"]


def test_fetch_pubmed_combines_three_calls():
    fake_get, calls = _router(esearch=ESEARCH_HITS, esummary=ESUMMARY,
                              efetch_text=EFETCH_XML)
    with patch.object(pubmed.time, "sleep"), \
            patch.object(pubmed.requests, "get", side_effect=fake_get):
        out = pubmed.fetch_pubmed("BMP7 AND coloboma", retmax=40)
    assert out["esearch"]["idlist"] == ["111", "222"]
    assert out["esummary"] == ESUMMARY
    assert out["efetch"]["111"]["mesh"] == ["Coloboma", "Zebrafish"]
    assert any("esearch" in u for u, _ in calls)
    assert any("efetch" in u for u, _ in calls)


def test_fetch_pubmed_empty_search_skips_summary_and_fetch():
    fake_get, calls = _router(esearch=ESEARCH_EMPTY)
    with patch.object(pubmed.time, "sleep"), \
            patch.object(pubmed.requests, "get", side_effect=fake_get):
        out = pubmed.fetch_pubmed("nonsense query", retmax=40)
    assert out["esummary"] is None
    assert out["efetch"] == {}
    assert not any("esummary" in u for u, _ in calls)
    assert not any("efetch" in u for u, _ in calls)


def test_api_key_and_email_attached_when_configured(monkeypatch):
    monkeypatch.setenv("NCBI_API_KEY", "KEY9")
    monkeypatch.setenv("NCBI_EMAIL", "x@y.z")
    fake_get, calls = _router(esearch=ESEARCH_EMPTY)
    with patch.object(pubmed.requests, "get", side_effect=fake_get):
        pubmed.esearch("BMP7", retmax=5)
    _, params = calls[0]
    assert params["api_key"] == "KEY9"
    assert params["email"] == "x@y.z"
    assert params["tool"] == "bio-toolkit"
