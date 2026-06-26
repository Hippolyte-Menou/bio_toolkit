"""
Unit tests for bio_toolkit.clients.panelapp. No live network.

HTTP is mocked by monkeypatching the module's internal ``_get`` (the single
seam through which every download flows) to return a fake Response carrying a
representative PanelApp TSV. We assert the unified superset schema: gene
symbols + confidence are present, inheritance is mapped to short notation, and
panel id/name are stamped onto every record.
"""

import csv
import io

import pytest

from bio_toolkit.clients import panelapp


# A representative slice of a real PanelApp England TSV. Columns are a superset
# of those used by the three reconciled sources — the parser keys off the named
# columns "Entity type", "Gene Symbol", "GEL_Status", "Model_Of_Inheritance".
FAKE_TSV = (
    "Gene Symbol\tEntity Name\tEntity type\tGEL_Status\tSources(; separated)\t"
    "Model_Of_Inheritance\tPhenotypes\n"
    # Green gene, biallelic -> AR
    "OTX2\tOTX2\tgene\t3\tExpert Review Green\t"
    "BIALLELIC, autosomal or pseudoautosomal\tMicrophthalmia\n"
    # Green gene, X-linked -> XL
    "NDP\tNDP\tgene\t3\tExpert Review Green\t"
    "X-LINKED: hemizygous mutation in males, biallelic mutations in females\t"
    "Norrie disease\n"
    # Amber gene, monoallelic -> AD
    "PAX6\tPAX6\tgene\t2\tExpert Review Amber\t"
    "MONOALLELIC, autosomal or pseudoautosomal, NOT imprinted\tAniridia\n"
    # Red gene (status 1) -> excluded by green_amber_only default
    "SOX2\tSOX2\tgene\t1\tExpert Review Red\t"
    "MONOALLELIC, autosomal or pseudoautosomal, NOT imprinted\tAnophthalmia\n"
    # A non-gene entity (STR/region) -> always skipped
    "FMR1_CGG\tFMR1_CGG\tstr\t3\tExpert Review Green\t\tFragile X\n"
    # Empty symbol -> skipped
    "\t\tgene\t3\tExpert Review Green\tOther\t\n"
)


class _FakeResponse:
    def __init__(self, text):
        self.text = text
        self.status_code = 200

    def raise_for_status(self):
        return None


@pytest.fixture
def patched_get(monkeypatch):
    """Patch the module's HTTP seam so no live request is made."""
    calls = {"urls": []}

    def fake_get(url):
        calls["urls"].append(url)
        return _FakeResponse(FAKE_TSV)

    monkeypatch.setattr(panelapp, "_get", fake_get)
    return calls


# --- parse_panel_tsv (pure, no network) ---

def test_parse_returns_unified_schema():
    records = panelapp.parse_panel_tsv(FAKE_TSV, panel_id=34,
                                       panel_name="Anophthalmia or microphthalmia")
    # Green + Amber genes only by default: OTX2, NDP, PAX6 (SOX2 red dropped,
    # the STR row dropped, the empty-symbol row dropped).
    symbols = {r["symbol"] for r in records}
    assert symbols == {"OTX2", "NDP", "PAX6"}

    # Every record carries the full superset of fields.
    for r in records:
        assert set(r) == {
            "symbol", "confidence", "inheritance",
            "panel_id", "panel_name", "gel_status",
        }
        assert r["panel_id"] == 34
        assert r["panel_name"] == "Anophthalmia or microphthalmia"
        # Symbols + confidence are present (the headline unified assertion).
        assert r["symbol"]
        assert r["confidence"] in {"green", "amber", "red", "grey"}


def test_parse_confidence_and_inheritance_mapping():
    by_symbol = {
        r["symbol"]: r
        for r in panelapp.parse_panel_tsv(FAKE_TSV, panel_id=34)
    }
    assert by_symbol["OTX2"]["confidence"] == "green"
    assert by_symbol["OTX2"]["inheritance"] == "AR"
    assert by_symbol["OTX2"]["gel_status"] == "3"

    assert by_symbol["NDP"]["confidence"] == "green"
    assert by_symbol["NDP"]["inheritance"] == "XL"

    assert by_symbol["PAX6"]["confidence"] == "amber"
    assert by_symbol["PAX6"]["inheritance"] == "AD"
    assert by_symbol["PAX6"]["gel_status"] == "2"


def test_parse_green_amber_only_excludes_red():
    symbols = {
        r["symbol"] for r in panelapp.parse_panel_tsv(FAKE_TSV, panel_id=34)
    }
    assert "SOX2" not in symbols


def test_parse_full_includes_red_when_flag_off():
    records = panelapp.parse_panel_tsv(FAKE_TSV, panel_id=34,
                                       green_amber_only=False)
    by_symbol = {r["symbol"]: r for r in records}
    assert "SOX2" in by_symbol
    assert by_symbol["SOX2"]["confidence"] == "red"
    # STR / empty-symbol rows are still excluded regardless of the flag.
    assert "FMR1_CGG" not in by_symbol
    assert "" not in by_symbol


def test_parse_skips_non_gene_and_empty_rows():
    records = panelapp.parse_panel_tsv(FAKE_TSV, panel_id=34,
                                       green_amber_only=False)
    symbols = [r["symbol"] for r in records]
    assert "FMR1_CGG" not in symbols  # str entity
    assert "" not in symbols          # empty symbol


# --- fetch_panel / fetch_panels (mocked HTTP) ---

def test_fetch_panel_uses_mocked_http(patched_get):
    records = panelapp.fetch_panel(34, panel_name="Anophthalmia or microphthalmia")
    symbols = {r["symbol"] for r in records}
    assert symbols == {"OTX2", "NDP", "PAX6"}
    # The download URL was built from the panel id and hit our seam.
    assert patched_get["urls"] == [
        "https://panelapp.genomicsengland.co.uk/panels/34/download/01234/"
    ]
    # Confidence present on every record (unified schema assertion).
    assert all(r["confidence"] for r in records)


def test_fetch_panels_flattens_across_panels(patched_get):
    panels = {34: "Anophthalmia or microphthalmia", 294: "Ocular coloboma"}
    records = panelapp.fetch_panels(panels)
    # Two panels x 3 green/amber genes each = 6 flat records.
    assert len(records) == 6
    panel_ids = {r["panel_id"] for r in records}
    assert panel_ids == {34, 294}
    # Both panels were downloaded.
    assert len(patched_get["urls"]) == 2


def test_fetch_panel_is_list_of_dicts(patched_get):
    records = panelapp.fetch_panel(34)
    assert isinstance(records, list)
    assert all(isinstance(r, dict) for r in records)


# --- highest_confidence reconciliation helper ---

def test_highest_confidence_priority():
    assert panelapp.highest_confidence(["amber", "green", "red"]) == "green"
    assert panelapp.highest_confidence(["red", "amber"]) == "amber"
    assert panelapp.highest_confidence(["red"]) == "red"
    assert panelapp.highest_confidence([]) == "grey"


def test_fake_tsv_is_well_formed():
    # Guard: the fixture TSV parses as a tab table with the named columns the
    # client relies on.
    reader = csv.DictReader(io.StringIO(FAKE_TSV), delimiter="\t")
    assert "Gene Symbol" in reader.fieldnames
    assert "GEL_Status" in reader.fieldnames
    assert "Model_Of_Inheritance" in reader.fieldnames
    assert "Entity type" in reader.fieldnames
