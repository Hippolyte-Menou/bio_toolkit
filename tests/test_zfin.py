"""Unit tests for bio_toolkit.clients.zfin. HTTP is mocked; no network.

ZFIN has no per-gene API -- it downloads three bulk TSV files into a cache and
indexes them. The tests mock the streaming download and point cache_dir at a
tmp_path, so each test gets an isolated cache (and a distinct memoisation key).
"""

import requests

from bio_toolkit.clients import zfin


# --- fixture TSV content (tab-separated, column layout per the client docs) ---

# human_orthos: cols 0=zdb 1=zf_sym 2=zf_name 3=human_sym ... 8=evidence 9=pub
HUMAN_ORTHOS = "\t".join([
    "ZDB-GENE-001", "pax6a", "paired box 6a", "PAX6", "paired box 6",
    "OMIM:607108", "HGNC:8620", "5080", "AA", "ZDB-PUB-001", "desc", "ECO:1", "ecodesc",
]) + "\n"

# phenotype: col 2=zdb 7=struct_id 8=struct_name 9=qual_id 10=qual_name 11=tag
#            18=fish_id 19=fish_name 20=start 21=end 23=pub
PHENO_ROW = "\t".join([
    "0", "pax6a", "ZDB-GENE-001", "3", "4", "5", "6",
    "ZFA:0000047", "eye", "PATO:0000638", "absent", "abnormal",
    "12", "13", "14", "15", "16", "17",
    "ZDB-FISH-1", "pax6a mutant", "stage-1", "stage-5", "22", "ZDB-PUB-2",
]) + "\n"

# expression: col 0=zdb 2=genotype 3=anatomy_id 4=anatomy_name
#             7=start 8=end 9=assay 11=pub
EXPR_ROW = "\t".join([
    "ZDB-GENE-001", "pax6a", "WT", "ZFA:0000047", "retina", "5", "6",
    "stage-1", "stage-3", "ISH", "10", "ZDB-PUB-3",
]) + "\n"

FILES_BY_NAME = {
    "human_orthos": HUMAN_ORTHOS.encode("utf-8"),
    "phenoGeneCleanData_fish": PHENO_ROW.encode("utf-8"),
    "wildtype-expression_fish": EXPR_ROW.encode("utf-8"),
}


class FakeStreamResponse:
    """Mimics requests.get(..., stream=True) used as a context manager."""

    def __init__(self, content: bytes):
        self._content = content
        self.status_code = 200

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def raise_for_status(self):
        pass

    def iter_content(self, chunk_size=1):
        yield self._content


def _install_fake_download(monkeypatch):
    def fake_get(url, timeout=None, stream=None):
        for key, content in FILES_BY_NAME.items():
            if key in url:
                return FakeStreamResponse(content)
        raise AssertionError(f"unexpected URL {url}")

    monkeypatch.setattr(zfin.requests, "get", fake_get)


def test_fetch_zfin_returns_raw_orthologs_phenos_expression(monkeypatch, tmp_path):
    _install_fake_download(monkeypatch)

    out = zfin.fetch_zfin("PAX6", cache_dir=tmp_path)

    assert out["gene"] == "PAX6"

    # Ortholog row resolved by human symbol.
    assert len(out["orthologs"]) == 1
    assert out["orthologs"][0]["zdb"] == "ZDB-GENE-001"
    assert out["orthologs"][0]["zf_sym"] == "pax6a"
    assert out["orthologs"][0]["evidence"] == "AA"

    # RAW phenotype rows (no ocular filtering): the single eye row is returned.
    assert len(out["phenotypes"]) == 1
    p = out["phenotypes"][0]
    assert p["structure_name"] == "eye"
    assert p["quality_name"] == "absent"
    assert p["tag"] == "abnormal"
    assert p["fish_name"] == "pax6a mutant"

    # RAW expression rows.
    assert len(out["expression"]) == 1
    e = out["expression"][0]
    assert e["anatomy_name"] == "retina"
    assert e["genotype"] == "WT"
    assert e["assay"] == "ISH"


def test_fetch_zfin_no_ortholog_returns_empty(monkeypatch, tmp_path):
    _install_fake_download(monkeypatch)

    out = zfin.fetch_zfin("NOSUCHGENE", cache_dir=tmp_path)
    assert out["gene"] == "NOSUCHGENE"
    assert out["orthologs"] == []
    assert out["phenotypes"] == []
    assert out["expression"] == []


def test_fetch_zfin_upper_cases_gene(monkeypatch, tmp_path):
    _install_fake_download(monkeypatch)

    out = zfin.fetch_zfin("pax6", cache_dir=tmp_path)
    assert out["gene"] == "PAX6"
    assert len(out["orthologs"]) == 1


def test_fetch_zfin_caches_downloads_once(monkeypatch, tmp_path):
    downloads = {"n": 0}

    def counting_get(url, timeout=None, stream=None):
        downloads["n"] += 1
        for key, content in FILES_BY_NAME.items():
            if key in url:
                return FakeStreamResponse(content)
        raise AssertionError(url)

    monkeypatch.setattr(zfin.requests, "get", counting_get)

    zfin.fetch_zfin("PAX6", cache_dir=tmp_path)
    first = downloads["n"]
    assert first == 3  # three bulk files downloaded on first call

    # Second call: indexes are memoised for this cache dir -> no new downloads.
    zfin.fetch_zfin("PAX6", cache_dir=tmp_path)
    assert downloads["n"] == first


def test_fetch_zfin_reuses_fresh_cache_files(monkeypatch, tmp_path):
    """A fresh cache file on disk is not re-downloaded even with a cold index."""
    _install_fake_download(monkeypatch)
    # Prime the cache files.
    zfin.fetch_zfin("PAX6", cache_dir=tmp_path)
    # Drop the in-memory index so _ensure_indexes rebuilds from disk.
    zfin._ZFIN_INDEXES.pop(tmp_path, None)

    downloads = {"n": 0}

    def counting_get(url, timeout=None, stream=None):
        downloads["n"] += 1
        return FakeStreamResponse(b"")

    monkeypatch.setattr(zfin.requests, "get", counting_get)

    out = zfin.fetch_zfin("PAX6", cache_dir=tmp_path)
    assert downloads["n"] == 0  # files are fresh -> no download
    assert len(out["orthologs"]) == 1
