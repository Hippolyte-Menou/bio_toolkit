"""Unit tests for bio_toolkit.clients.uniprot. HTTP mocked, no network."""

import pytest

from bio_toolkit.clients import uniprot


class FakeResponse:
    def __init__(self, payload=None, text="", status_code=200):
        self._payload = payload
        self.text = text
        self.status_code = status_code

    def json(self):
        return self._payload


# A trimmed UniProtKB entry shaped like rest.uniprot.org/uniprotkb/{acc}.json
P26367_ENTRY = {
    "proteinDescription": {
        "recommendedName": {
            "fullName": {"value": "Paired box protein Pax-6"},
            "shortNames": [{"value": "PAX6"}],
        },
        "alternativeNames": [
            {"fullName": {"value": "Aniridia type II protein"}, "shortNames": []}
        ],
        "flag": "",
    },
    "sequence": {"value": "MQNSHSGVNQLGG", "length": 422, "molWeight": 46683},
}

COORDS_BLOB = {
    "coordinates": {
        "gnCoordinate": [
            {
                "genomicLocation": {
                    "chromosome": "11",
                    "start": 31789026,
                    "end": 31817961,
                    "assemblyName": "GRCh38",
                }
            }
        ]
    }
}


# --- parsers (no HTTP) ---

def test_parse_protein_info():
    info = uniprot.parse_protein_info(P26367_ENTRY)
    assert info["protein_name"] == "Paired box protein Pax-6"
    assert info["protein_alias"] == "Aniridia type II protein"
    assert info["protein_sequence"] == "MQNSHSGVNQLGG"
    assert info["protein_size_aa"] == 422
    assert info["molecular_weight"] == 46683


def test_parse_protein_info_empty_entry_all_defaults():
    info = uniprot.parse_protein_info({})
    assert info == {
        "protein_name": "",
        "protein_alias": "",
        "protein_sequence": "",
        "protein_size_aa": 0,
        "molecular_weight": 0,
    }


def test_parse_protein_nomenclature():
    nom = uniprot.parse_protein_nomenclature(P26367_ENTRY)
    assert nom["recommended_full_name"] == "Paired box protein Pax-6"
    assert nom["recommended_short_names"] == ["PAX6"]
    assert nom["alternative_names"] == [
        {"fullName": "Aniridia type II protein", "shortNames": []}
    ]
    assert nom["flag"] == ""


def test_parse_genomic_coordinates():
    coords = uniprot.parse_genomic_coordinates(COORDS_BLOB)
    assert coords == ("11", 31789026, 31817961, "GRCh38")


def test_parse_genomic_coordinates_missing_returns_none():
    assert uniprot.parse_genomic_coordinates({}) is None


# --- fetchers (HTTP mocked via internal _get) ---

def test_fetch_uniprotkb_success(monkeypatch):
    monkeypatch.setattr(uniprot, "_get", lambda url: FakeResponse(payload=P26367_ENTRY))
    result = uniprot.fetch_uniprotkb("P26367")
    assert result["success"] is True
    assert result["data"] == P26367_ENTRY


def test_fetch_uniprotkb_http_error(monkeypatch):
    monkeypatch.setattr(uniprot, "_get", lambda url: FakeResponse(status_code=404))
    result = uniprot.fetch_uniprotkb("BADACC")
    assert result["success"] is False
    assert result["uniprot_id"] == "BADACC"
    assert result["error"] == "HTTP 404"


def test_fetch_uniprotkb_request_exception(monkeypatch):
    import requests

    def boom(url):
        raise requests.exceptions.ConnectionError("down")

    monkeypatch.setattr(uniprot, "_get", boom)
    result = uniprot.fetch_uniprotkb("P26367")
    assert result["success"] is False
    assert "down" in result["error"]


def test_fetch_ebi_json_endpoint(monkeypatch):
    monkeypatch.setattr(uniprot, "_get", lambda url: FakeResponse(payload={"features": []}))
    result = uniprot.fetch_ebi("variation", "P26367")
    assert result["success"] is True
    assert result["type"] == "variation"
    assert result["data"] == {"features": []}


def test_fetch_ebi_csv_endpoint_returns_text(monkeypatch):
    monkeypatch.setattr(uniprot, "_get", lambda url: FakeResponse(text="a,b,c\n1,2,3"))
    result = uniprot.fetch_ebi("alphafold", "P26367")
    assert result["success"] is True
    assert result["data"] == "a,b,c\n1,2,3"


def test_fetch_protein_assembles_blob(monkeypatch):
    def fake_get(url):
        if url.endswith(".csv"):
            return FakeResponse(text="csvdata")
        if "uniprotkb" in url:
            return FakeResponse(payload=P26367_ENTRY)
        return FakeResponse(payload={"ok": True})

    monkeypatch.setattr(uniprot, "_get", fake_get)
    blob = uniprot.fetch_protein("P26367")

    assert blob["uniprot_id"] == "P26367"
    assert blob["basic_data"] == P26367_ENTRY
    assert blob["additional_data"]["alphafold"] == "csvdata"
    assert blob["additional_data"]["variation"] == {"ok": True}


def test_fetch_protein_failed_ebi_endpoint_is_none(monkeypatch):
    def fake_get(url):
        if "uniprotkb" in url:
            return FakeResponse(payload=P26367_ENTRY)
        return FakeResponse(status_code=500)

    monkeypatch.setattr(uniprot, "_get", fake_get)
    blob = uniprot.fetch_protein("P26367")
    assert blob["additional_data"]["variation"] is None


def test_fetch_protein_no_ebi(monkeypatch):
    monkeypatch.setattr(uniprot, "_get", lambda url: FakeResponse(payload=P26367_ENTRY))
    blob = uniprot.fetch_protein("P26367", include_ebi=False)
    assert "additional_data" not in blob


def test_lookup_protein_returns_raw_and_parsed(monkeypatch):
    monkeypatch.setattr(uniprot, "_get", lambda url: FakeResponse(payload=P26367_ENTRY))
    out = uniprot.lookup_protein("P26367", include_ebi=False)

    assert out["accession"] == "P26367"
    assert out["protein_info"]["protein_name"] == "Paired box protein Pax-6"
    assert out["nomenclature"]["recommended_short_names"] == ["PAX6"]
    assert out["raw"]["basic_data"] == P26367_ENTRY


def test_lookup_protein_failed_fetch_parses_defaults(monkeypatch):
    monkeypatch.setattr(uniprot, "_get", lambda url: FakeResponse(status_code=404))
    out = uniprot.lookup_protein("BADACC", include_ebi=False)

    assert out["protein_info"]["protein_name"] == ""
    assert out["raw"]["basic_data"]["success"] is False
