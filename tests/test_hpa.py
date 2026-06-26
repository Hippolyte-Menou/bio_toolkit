"""Unit tests for bio_toolkit.clients.hpa. HTTP is mocked; no network."""

import requests

from bio_toolkit.clients import hpa


class FakeResponse:
    def __init__(self, *, json_data=None, content=b"", status_code=200):
        self._json = json_data
        self.content = content
        self.status_code = status_code
        self.url = "https://example.test/mock"

    def json(self):
        return self._json

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.exceptions.HTTPError(f"status {self.status_code}")


SD_ROWS = [
    {"Gene": "PAX6", "Ensembl": "ENSG00000007372", "RNA tissue specificity": "tissue enhanced"},
    {"Gene": "OTHER", "Ensembl": "ENSG00000000000"},
]

GENE_XML = b"""<?xml version="1.0"?>
<proteinAtlas>
  <entry>
    <name>PAX6</name>
    <tissueExpression>
      <tissue organ="Eye">Retina</tissue>
      <level>High</level>
    </tissueExpression>
    <summary type="tissue">Expressed in retina and lens.</summary>
  </entry>
</proteinAtlas>
"""


def test_fetch_hpa_resolves_ensembl_and_parses_xml(monkeypatch):
    calls = []

    def fake_get(url, params=None, headers=None, timeout=None):
        calls.append((url, params))
        if "search_download" in url:
            return FakeResponse(json_data=SD_ROWS)
        if url.endswith(".xml"):
            return FakeResponse(content=GENE_XML)
        raise AssertionError(f"unexpected URL {url}")

    monkeypatch.setattr(hpa.requests, "get", fake_get)

    out = hpa.fetch_hpa("PAX6")

    # Ensembl resolved from the matching search_download row.
    assert out["ensembl_id"] == "ENSG00000007372"
    assert out["search_download"] == SD_ROWS
    assert out["search_download_match"]["Gene"] == "PAX6"
    assert out["errors"] == {}

    # XML parsed into flat nodes (raw, unfiltered).
    tags = {n["tag"] for n in out["xml_nodes"]}
    assert "tissue" in tags
    assert "summary" in tags
    tissue_node = next(n for n in out["xml_nodes"] if n["tag"] == "tissue")
    assert tissue_node["attrib"]["organ"] == "Eye"
    assert tissue_node["text"] == "Retina"

    # The .xml URL used the resolved Ensembl id.
    assert any(u.endswith("ENSG00000007372.xml") for u, _ in calls)


def test_fetch_hpa_uses_supplied_ensembl_id(monkeypatch):
    def fake_get(url, params=None, headers=None, timeout=None):
        if "search_download" in url:
            return FakeResponse(json_data=[])
        if url.endswith(".xml"):
            assert "ENSG00000123456.xml" in url
            return FakeResponse(content=GENE_XML)
        raise AssertionError(url)

    monkeypatch.setattr(hpa.requests, "get", fake_get)

    out = hpa.fetch_hpa("FOO", ensembl_id="ENSG00000123456")
    assert out["ensembl_id"] == "ENSG00000123456"
    assert out["xml_nodes"]  # XML still fetched even with empty search_download


def test_fetch_hpa_captures_xml_error_keeps_search_download(monkeypatch):
    def fake_get(url, params=None, headers=None, timeout=None):
        if "search_download" in url:
            return FakeResponse(json_data=SD_ROWS)
        # XML endpoint dead.
        return FakeResponse(status_code=404)

    monkeypatch.setattr(hpa.requests, "get", fake_get)

    out = hpa.fetch_hpa("PAX6")
    assert out["search_download"] == SD_ROWS
    assert out["ensembl_id"] == "ENSG00000007372"
    assert "xml" in out["errors"]
    assert out["xml_nodes"] == []


def test_fetch_hpa_skips_xml_without_ensembl(monkeypatch):
    def fake_get(url, params=None, headers=None, timeout=None):
        assert "search_download" in url, "should not hit XML without ensembl"
        return FakeResponse(json_data=[])  # no rows -> no ensembl resolved

    monkeypatch.setattr(hpa.requests, "get", fake_get)

    out = hpa.fetch_hpa("NOPE")
    assert out["ensembl_id"] is None
    assert out["xml_nodes"] == []
    assert out["errors"] == {}


def test_parse_hpa_xml_handles_parse_error():
    nodes = hpa._parse_hpa_xml(b"<not valid xml")
    assert len(nodes) == 1
    assert nodes[0]["tag"] == "_parse_error"
