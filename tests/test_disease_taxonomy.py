"""Unit tests for bio_toolkit.refdata.disease_taxonomy. No live network."""

import re

import pytest

from bio_toolkit.refdata import disease_taxonomy as dt


# --- pure transforms ---

def test_strip_trailing_number():
    assert dt.strip_trailing_number("Retinitis pigmentosa 3") == "Retinitis pigmentosa"
    assert dt.strip_trailing_number("Cone-rod dystrophy X-linked 1") == "Cone-rod dystrophy X-linked"
    # trailing comma left after stripping the number is removed
    assert dt.strip_trailing_number("Macular degeneration age-related, 2") == "Macular degeneration age-related"
    # meaningful inner numbers are preserved
    assert dt.strip_trailing_number("Oculocutaneous albinism type 1A") == "Oculocutaneous albinism type 1A"


def test_guess_pathology_tag_first_match_wins():
    rules = [
        (re.compile(r"retinitis pigmentosa", re.IGNORECASE), "retina/rp"),
        (re.compile(r"retinitis", re.IGNORECASE), "retina/other"),
    ]
    tag, confident = dt.guess_pathology_tag("Retinitis pigmentosa 7", rules)
    assert (tag, confident) == ("retina/rp", True)


def test_guess_pathology_tag_no_match_needs_review():
    rules = [(re.compile(r"glaucoma", re.IGNORECASE), "glaucoma")]
    tag, confident = dt.guess_pathology_tag("Some unmapped disease", rules)
    assert tag == ""
    assert confident is False


def test_get_uniprot_id_takes_first():
    assert dt.get_uniprot_id({"uniprot_ids": "P26367|Q99999"}) == "P26367"
    assert dt.get_uniprot_id({"uniprot_ids": ""}) is None
    assert dt.get_uniprot_id({"uniprot_ids": "nan"}) is None
    assert dt.get_uniprot_id({}) is None


def test_extract_diseases_from_uniprot_json():
    entry = {
        "comments": [
            {"commentType": "FUNCTION"},  # ignored
            {
                "commentType": "DISEASE",
                "disease": {
                    "diseaseId": "Aniridia",
                    "acronym": "AN",
                    "description": "An eye disorder.",
                    "diseaseCrossReference": {"database": "MIM", "id": "106210"},
                },
                "evidences": [
                    {"source": "PubMed", "id": "1971"},
                    {"source": "UniProt", "id": "x"},  # ignored
                ],
            },
            {"commentType": "DISEASE", "disease": {}},  # empty -> skipped
        ]
    }
    out = dt.extract_diseases_from_uniprot_json(entry)
    assert len(out) == 1
    d = out[0]
    assert d["disease_name"] == "Aniridia"
    assert d["acronym"] == "AN"
    assert d["omim_id"] == "106210"
    assert d["pubmed_ids"] == ["1971"]


def test_build_disease_lookup_aggregates_genes_and_review():
    rules = [(re.compile(r"aniridia", re.IGNORECASE), "anterior/aniridia")]
    gene_diseases = {
        "PAX6": [
            {"acronym": "AN", "disease_name": "Aniridia 1", "omim_id": "106210"},
            {"acronym": "FOO", "disease_name": "Mystery syndrome", "omim_id": ""},
        ],
        "ELP4": [
            {"acronym": "AN", "disease_name": "Aniridia 1", "omim_id": "106210"},
        ],
    }
    lookup, tag_mapping, review = dt.build_disease_lookup(gene_diseases, rules)

    # AN aggregates both genes; confident via the rule
    assert set(lookup["AN"]["genes"]) == {"PAX6", "ELP4"}
    assert lookup["AN"]["pathology_tag"] == "anterior/aniridia"
    assert lookup["AN"]["confident"] is True
    assert lookup["AN"]["group_name"] == "Aniridia"

    # FOO is unmapped -> in review, not confident
    assert lookup["FOO"]["confident"] is False
    review_names = {(r["disease_name"], r["gene"]) for r in review}
    assert ("Mystery syndrome", "PAX6") in review_names

    # group-level mapping keyed by stripped name
    assert "Aniridia" in tag_mapping
    assert set(tag_mapping["Aniridia"]["genes"]) == {"PAX6", "ELP4"}


# --- file readers (tmp fixtures) ---

def test_load_pathology_rules(tmp_path):
    yml = tmp_path / "taxonomy.yaml"
    yml.write_text(
        "pathology_tag_rules:\n"
        "  - pattern: 'retinitis pigmentosa'\n"
        "    tag: 'retina/rp'\n"
        "  - pattern: 'glaucoma'\n"
        "    tag: 'glaucoma'\n",
        encoding="utf-8",
    )
    rules = dt.load_pathology_rules(yml)
    assert len(rules) == 2
    tag, confident = dt.guess_pathology_tag("Retinitis pigmentosa 4", rules)
    assert (tag, confident) == ("retina/rp", True)


def test_extract_panelapp_genes_dedup_order(tmp_path):
    table = tmp_path / "gene_table.md"
    table.write_text(
        "| [[G - Genes/PAX6\\|PAX6]] | something |\n"
        "| [[G - Genes/RPGR|RPGR]] | other |\n"
        "| [[G - Genes/pax6\\|pax6]] | dup |\n",  # case-insensitive dup of PAX6
        encoding="utf-8",
    )
    genes = dt.extract_panelapp_genes(table)
    assert genes == ["PAX6", "RPGR"]


def test_extract_panelapp_genes_missing_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        dt.extract_panelapp_genes(tmp_path / "nope.md")


def test_load_hgnc_index(tmp_path):
    tsv = tmp_path / "hgnc.txt"
    tsv.write_text(
        "symbol\tuniprot_ids\tname\n"
        "PAX6\tP26367\tpaired box 6\n"
        "rpgr\tQ92834\tRP GTPase regulator\n",
        encoding="utf-8",
    )
    index = dt.load_hgnc_index(tsv)
    assert "PAX6" in index and "RPGR" in index  # keys uppercased
    assert dt.get_uniprot_id(index["PAX6"]) == "P26367"


# --- HTTP fetchers (mocked) ---

class _Resp:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


def test_fetch_batch_diseases_maps_accessions(monkeypatch):
    payload = {
        "results": [
            {
                "primaryAccession": "P26367",
                "comments": [
                    {
                        "commentType": "DISEASE",
                        "disease": {
                            "diseaseId": "Aniridia",
                            "acronym": "AN",
                            "diseaseCrossReference": {"database": "MIM", "id": "106210"},
                        },
                        "evidences": [],
                    }
                ],
            }
        ]
    }

    def fake_get(url, params=None, timeout=dt.REQUEST_TIMEOUT):
        return _Resp(200, payload)

    monkeypatch.setattr(dt, "_http_get", fake_get)

    accession_map = {"P26367": "PAX6", "Q00000": "MISSING"}
    out = dt.fetch_batch_diseases(accession_map)
    assert out["PAX6"][0]["disease_name"] == "Aniridia"
    # accession not in response -> empty list
    assert out["MISSING"] == []


def test_fetch_batch_diseases_empty_input():
    assert dt.fetch_batch_diseases({}) == {}


def test_query_all_genes_uses_cache_and_batches(monkeypatch):
    # one cached gene, one fetched via batch, one without a UniProt id
    hgnc_index = {
        "PAX6": {"symbol": "PAX6", "uniprot_ids": "P26367"},
        "NOUP": {"symbol": "NOUP", "uniprot_ids": ""},
    }
    cache = {"RPGR": [{"disease_name": "RP", "acronym": "RP"}]}

    def fake_batch(accession_map):
        # only PAX6 should be pending here
        assert accession_map == {"P26367": "PAX6"}
        return {"PAX6": [{"disease_name": "Aniridia", "acronym": "AN"}]}

    monkeypatch.setattr(dt, "fetch_batch_diseases", fake_batch)

    results = dt.query_all_genes(
        ["PAX6", "RPGR", "NOUP"], hgnc_index, cache=cache, batch_delay=0
    )
    assert results["PAX6"][0]["disease_name"] == "Aniridia"
    assert results["RPGR"][0]["acronym"] == "RP"      # from cache
    assert results["NOUP"] == []                       # no UniProt id
    # cache updated with the freshly fetched gene
    assert cache["PAX6"][0]["acronym"] == "AN"


def test_query_all_genes_skip_api(monkeypatch):
    def boom(_):
        raise AssertionError("network should not be touched in skip_api mode")

    monkeypatch.setattr(dt, "fetch_batch_diseases", boom)

    cache = {"PAX6": [{"disease_name": "Aniridia"}]}
    results = dt.query_all_genes(["PAX6", "OTHER"], {}, cache=cache, skip_api=True)
    assert results == {"PAX6": [{"disease_name": "Aniridia"}]}
