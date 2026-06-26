"""Unit tests for bio_toolkit.clients.varaico. HTTP + bigBed mocked, no network."""

import gzip
import io
import json
from unittest.mock import MagicMock, patch

import pytest

from bio_toolkit.clients.varaico import (
    VaraicoClient,
    parse_record,
    parse_refgene_coordinates,
)


# --- refGene parsing ---

def _refgene_gz(rows):
    """Build a gzipped refGene-style .txt for given (chrom, start, end, name2) rows."""
    lines = []
    for chrom, start, end, name2 in rows:
        # refGene columns: bin, name, chrom, strand, txStart, txEnd, ... name2 at idx 12
        cols = ["0", "NM_x", chrom, "+", str(start), str(end),
                "0", "0", "1", "0", "0", "0", name2]
        # pad to >= 13 columns (already 13)
        lines.append("\t".join(cols))
    raw = ("\n".join(lines) + "\n").encode("utf-8")
    return gzip.compress(raw)


def test_parse_refgene_merges_transcripts():
    gz = _refgene_gz([
        ("chr11", 31000000, 31010000, "PAX6"),
        ("chr11", 31005000, 31020000, "PAX6"),   # merges -> min start, max end
        ("chr2_alt", 1, 2, "JUNK"),               # alt contig skipped
        ("chrX", 500, 600, "RPGR"),
    ])
    coords = parse_refgene_coordinates(gz)
    assert coords["PAX6"] == {"chrom": "chr11", "start": 31000000, "end": 31020000}
    assert "JUNK" not in coords
    assert coords["RPGR"]["chrom"] == "chrX"


# --- parse_record (bigBed record tuple) ---

def _bb_record():
    """A synthetic pybigtools record tuple parseable by parse_record.

    Layout: [start, end, gene, variant_name, pad, pad, pad,
             outlink, pmid, textSection, articlesCount,
             author x4, year, title..., <back 8 fields>]
    Back 8 (from end): ref, alt, hgncSymbol, refseq, cdot, pdot, effect, doi
    """
    front = ["100", "200", "PAX6", "var1", "x", "x", "x"]
    anchor = ["https://varaico.com/v/1", "12345678", "results", "3"]
    middle = ["Jane", "Roe", "et", "al", "2021", "A", "study", "of", "PAX6"]
    back = ["G", "A", "PAX6", "NM_000280", "c.100G>A", "p.Gly34Arg",
            "missense", "10.1/xyz"]
    return tuple(front + anchor + middle + back)


def test_parse_record_extracts_fields():
    rec = parse_record("chr11", _bb_record())
    assert rec is not None
    assert rec["chromStart"] == 100
    assert rec["chromEnd"] == 200
    assert rec["hgncSymbol"] == "PAX6"
    assert rec["pmid"] == "12345678"
    assert rec["cdot"] == "c.100G>A"
    assert rec["pdot"] == "p.Gly34Arg"
    assert rec["effect"] == "missense"
    assert rec["ref"] == "G"
    assert rec["alt"] == "A"
    assert rec["doi"] == "10.1/xyz"
    assert rec["year"] == "2021"
    assert rec["outlink"] == "https://varaico.com/v/1"


def test_parse_record_rejects_short_tuple():
    assert parse_record("chr1", ("a", "b", "c")) is None


# --- coordinate download (HTTP mocked) ---

def test_download_gene_coordinates_via_requests(tmp_path):
    gz = _refgene_gz([("chr11", 31000000, 31020000, "PAX6")])
    resp = MagicMock()
    resp.content = gz
    resp.raise_for_status = MagicMock()

    cache = tmp_path / "coords.json"
    client = VaraicoClient(coord_cache=cache)
    with patch("bio_toolkit.clients.varaico.requests.get", return_value=resp) as mock_get:
        coords = client.download_gene_coordinates()

    mock_get.assert_called_once()
    assert coords["PAX6"]["chrom"] == "chr11"
    # cache written to disk
    assert json.loads(cache.read_text())["PAX6"]["start"] == 31000000


def test_load_coordinates_uses_cache(tmp_path):
    cache = tmp_path / "coords.json"
    cache.write_text(json.dumps({"PAX6": {"chrom": "chr11", "start": 1, "end": 9}}))
    client = VaraicoClient(coord_cache=cache)
    # no HTTP should happen; if it did, requests.get would fail without network
    coords = client.load_coordinates()
    assert coords["PAX6"]["end"] == 9


# --- query_gene_symbol (coords + bigBed reader mocked) ---

def test_query_gene_symbol_returns_parsed_variants(tmp_path):
    cache = tmp_path / "coords.json"
    cache.write_text(json.dumps(
        {"PAX6": {"chrom": "chr11", "start": 31000000, "end": 31020000}}
    ))
    client = VaraicoClient(varaico_bb="dummy.bb", coord_cache=cache)

    fake_bb = MagicMock()
    fake_bb.records.return_value = iter([_bb_record()])

    with patch.object(VaraicoClient, "_open_bb", return_value=fake_bb):
        variants = client.query_gene_symbol("PAX6")

    assert len(variants) == 1
    assert variants[0]["hgncSymbol"] == "PAX6"
    assert variants[0]["source"] == "main"
    assert variants[0]["pmid"] == "12345678"
    # region queried with padding applied
    chrom, start, end = fake_bb.records.call_args[0]
    assert chrom == "chr11"
    assert start == 31000000 - client.padding
    assert end == 31020000 + client.padding


def test_query_gene_symbol_unknown_gene(tmp_path):
    cache = tmp_path / "coords.json"
    cache.write_text(json.dumps({"PAX6": {"chrom": "chr11", "start": 1, "end": 9}}))
    client = VaraicoClient(varaico_bb="dummy.bb", coord_cache=cache)
    assert client.query_gene_symbol("NOSUCHGENE") == []


def test_query_gene_symbol_filters_other_genes(tmp_path):
    cache = tmp_path / "coords.json"
    cache.write_text(json.dumps(
        {"PAX6": {"chrom": "chr11", "start": 31000000, "end": 31020000}}
    ))
    client = VaraicoClient(varaico_bb="dummy.bb", coord_cache=cache)

    # one PAX6 record + one record for a different gene in the same window
    other = list(_bb_record())
    other[-6] = "OTHER"  # hgncSymbol position
    fake_bb = MagicMock()
    fake_bb.records.return_value = iter([_bb_record(), tuple(other)])

    with patch.object(VaraicoClient, "_open_bb", return_value=fake_bb):
        variants = client.query_gene_symbol("PAX6")

    assert len(variants) == 1
    assert variants[0]["hgncSymbol"] == "PAX6"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
