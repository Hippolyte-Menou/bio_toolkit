"""Unit tests for bio_toolkit.clients.openalex. HTTP mocked, no network."""

from unittest.mock import patch

import pytest

from bio_toolkit.clients.openalex import (
    OpenAlexClient,
    _invert_abstract,
    _strip_html,
    _tags_to_keywords,
)


# --- pure helpers ---

def test_strip_html():
    assert _strip_html("<scp>PAX6</scp> <i>gene</i>") == "PAX6 gene"
    assert _strip_html("") == ""


def test_invert_abstract_reconstructs_order():
    inv = {"PAX6": [0], "is": [1], "a": [2], "gene": [3]}
    assert _invert_abstract(inv) == "PAX6 is a gene"
    assert _invert_abstract(None) == ""


def test_tags_to_keywords_quotes_multiword():
    # hyphens -> spaces; any term containing a space is phrase-quoted
    out = _tags_to_keywords(["microphthalmia", "iris-coloboma", "optic disc"])
    assert out == ["microphthalmia", '"iris coloboma"', '"optic disc"']


def test_extract_pmid_strips_url():
    work = {"ids": {"pmid": "https://pubmed.ncbi.nlm.nih.gov/12345678/"}}
    assert OpenAlexClient.extract_pmid(work) == "12345678"
    assert OpenAlexClient.extract_pmid({"ids": {}}) is None


# --- search_gene (representative method, _get mocked) ---

def _fake_work(pmid, title, year=2024):
    return {
        "id": f"https://openalex.org/W{pmid}",
        "ids": {"pmid": f"https://pubmed.ncbi.nlm.nih.gov/{pmid}"},
        "title": title,
        "publication_year": year,
        "abstract_inverted_index": {title: [0]},
        "cited_by_count": 5,
        "authorships": [],
        "mesh": [],
    }


def test_search_gene_returns_parsed_works():
    client = OpenAlexClient(delay=0)
    page = {
        "results": [
            _fake_work("111", "PAX6 in aniridia"),
            _fake_work("222", "PAX6 coloboma cohort"),
        ],
        "meta": {"count": 2},
    }

    with patch.object(OpenAlexClient, "_get", return_value=page) as mock_get:
        works = client.search_gene(["PAX6"], disease_keywords=["coloboma"])

    assert [OpenAlexClient.extract_pmid(w) for w in works] == ["111", "222"]
    # one page fetched (batch < per_page short-circuits pagination)
    assert mock_get.call_count == 1
    # boolean query carried gene AND disease
    _endpoint, kwargs = mock_get.call_args
    flt = kwargs["params"]["filter"]
    assert "(PAX6) AND (coloboma)" in flt
    assert "has_pmid:true" in flt


def test_search_gene_text_exclusion():
    client = OpenAlexClient(delay=0)
    page = {
        "results": [
            _fake_work("111", "PAX6 in mouse retina"),
            _fake_work("222", "PAX6 human cohort"),
        ],
        "meta": {"count": 2},
    }
    with patch.object(OpenAlexClient, "_get", return_value=page):
        works = client.search_gene(["PAX6"], exclude_terms=["mouse"])

    pmids = [OpenAlexClient.extract_pmid(w) for w in works]
    assert pmids == ["222"]  # the "mouse" work dropped


def test_get_handles_404_as_none():
    client = OpenAlexClient(delay=0)

    class Resp:
        status_code = 404
        headers = {}

    with patch.object(OpenAlexClient, "_request", return_value=Resp()):
        assert client._get("/works", {"filter": "x"}) is None


def test_get_returns_json_and_increments_nothing_on_success():
    client = OpenAlexClient(delay=0)

    class Resp:
        status_code = 200
        headers = {}

        def raise_for_status(self):
            pass

        def json(self):
            return {"results": [], "meta": {"count": 0}}

    with patch.object(OpenAlexClient, "_request", return_value=Resp()):
        data = client._get("/works", {"filter": "x"})
    assert data == {"results": [], "meta": {"count": 0}}
    assert client.failed_request_count == 0


# --- work_to_record (raw parsed record, no rendering) ---

def test_work_to_record_flattens():
    work = {
        "ids": {
            "pmid": "https://pubmed.ncbi.nlm.nih.gov/999/",
            "doi": "https://doi.org/10.1/abc",
        },
        "title": "<i>PAX6</i> review",
        "publication_year": 2023,
        "publication_date": "2023-05-01",
        "primary_location": {"source": {"display_name": "Nature", "abbreviated_title": "Nat"}},
        "biblio": {"volume": "10", "issue": "2", "first_page": "1", "last_page": "9"},
        "authorships": [
            {"author": {"display_name": "Jane Roe"}, "raw_author_name": "Roe, Jane"},
            {"author": {"display_name": "John Smith"}, "raw_author_name": ""},
        ],
        "abstract_inverted_index": {"Hello": [0], "world": [1]},
        "type": "article",
        "cited_by_count": 7,
        "mesh": [{"descriptor_name": "Eye Abnormalities"}],
    }
    rec = OpenAlexClient.work_to_record(work)
    assert rec["pmid"] == "999"
    assert rec["doi"] == "10.1/abc"
    assert rec["title"] == "PAX6 review"  # html stripped
    assert ("Jane", "Roe") in rec["authors"]
    assert ("John", "Smith") in rec["authors"]
    assert rec["journal"] == "Nature"
    assert rec["pages"] == "1-9"
    assert rec["abstract"] == "Hello world"
    assert rec["mesh_terms"] == ["Eye Abnormalities"]


# --- filter_by_mesh static helper ---

def test_filter_by_mesh_keeps_empty_mesh():
    works = [
        {"id": "a", "mesh": []},
        {"id": "b", "mesh": [{"descriptor_name": "Neoplasms"}]},
        {"id": "c", "mesh": [{"descriptor_name": "Eye Abnormalities"}]},
    ]
    kept = OpenAlexClient.filter_by_mesh(works, ["Neoplasms"])
    kept_ids = {w["id"] for w in kept}
    assert kept_ids == {"a", "c"}  # empty-mesh kept, Neoplasms dropped


if __name__ == "__main__":
    pytest.main([__file__, "-v"])


from bio_toolkit.clients.openalex import OpenAlexClient


def test_fetch_works_by_ids_parses_and_filters(monkeypatch):
    client = OpenAlexClient()
    captured = {}

    def fake_get(endpoint, params=None):
        captured["params"] = params
        return {"results": [{"id": "https://openalex.org/W1", "title": "T"}]}

    monkeypatch.setattr(client, "_get", fake_get)
    works = client.fetch_works_by_ids(["https://openalex.org/W1", "W2"])
    assert works[0]["title"] == "T"
    assert "ids.openalex:W1|W2" in captured["params"]["filter"]


def test_search_by_title_builds_year_window(monkeypatch):
    client = OpenAlexClient()
    captured = {}

    def fake_get(endpoint, params=None):
        captured["params"] = params
        return {"results": [{"id": "W9"}]}

    monkeypatch.setattr(client, "_get", fake_get)
    got = client.search_by_title("Corneal dystrophy in DCN", year=2005)
    assert got and got[0]["id"] == "W9"
    f = captured["params"]["filter"]
    assert "title.search:Corneal dystrophy in DCN" in f
    assert "from_publication_date:2005-01-01" in f
    assert "to_publication_date:2005-12-31" in f
