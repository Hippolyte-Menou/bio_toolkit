"""Unit tests for bio_toolkit.util (retry, yaml, cache). No network."""

import pytest
import requests

from bio_toolkit.util.retry import retry_on_failure
from bio_toolkit.util.yaml import parse_frontmatter, extract_frontmatter, update_yaml_field
from bio_toolkit.util import cache


# --- retry ---

def test_retry_succeeds_after_transient_failures():
    calls = {"n": 0}

    @retry_on_failure(max_retries=3, base_delay=0)
    def flaky():
        calls["n"] += 1
        if calls["n"] < 3:
            raise requests.exceptions.ConnectionError("boom")
        return "ok"

    assert flaky() == "ok"
    assert calls["n"] == 3


def test_retry_reraises_after_exhausting():
    @retry_on_failure(max_retries=2, base_delay=0)
    def always_fail():
        raise requests.exceptions.Timeout("nope")

    with pytest.raises(requests.exceptions.Timeout):
        always_fail()


def test_retry_on_status_code():
    class Resp:
        def __init__(self, code):
            self.status_code = code

    seq = [Resp(503), Resp(503), Resp(200)]
    calls = {"i": 0}

    @retry_on_failure(max_retries=3, base_delay=0)
    def fetch():
        r = seq[calls["i"]]
        calls["i"] += 1
        return r

    assert fetch().status_code == 200
    assert calls["i"] == 3


# --- yaml ---

DOC = "---\ntitle: PAX6\nsource-quality: high\n---\n\nbody text\n"


def test_parse_frontmatter_scalars():
    fields = parse_frontmatter(DOC)
    assert fields["title"] == "PAX6"
    assert fields["source-quality"] == "high"


def test_extract_frontmatter_splits_body():
    fm, _sep, body = extract_frontmatter(DOC)
    assert "title: PAX6" in fm
    assert body.strip() == "body text"


def test_update_yaml_field_inserts_missing():
    out = update_yaml_field(DOC, "citekey", "menou2026")
    assert "citekey: menou2026" in out


# --- cache ---

def test_gene_cache_env_override(monkeypatch, tmp_path):
    monkeypatch.setenv("GENE_CACHE_DIR", str(tmp_path))
    assert cache.gene_cache_dir() == tmp_path


def test_default_dirs_resolve_by_name(monkeypatch):
    monkeypatch.delenv("GENE_CACHE_DIR", raising=False)
    monkeypatch.delenv("LITERATURE_DIR", raising=False)
    monkeypatch.delenv("PDF_CORPUS_DIR", raising=False)
    monkeypatch.delenv("GENETICS_ROOT", raising=False)
    assert cache.gene_cache_dir().name == "gene-cache"
    assert cache.literature_dir().name == "literature"


def test_pdf_corpus_is_pdfs_under_literature(monkeypatch):
    monkeypatch.delenv("PDF_CORPUS_DIR", raising=False)
    monkeypatch.delenv("LITERATURE_DIR", raising=False)
    monkeypatch.delenv("GENETICS_ROOT", raising=False)
    corpus = cache.pdf_corpus_dir()
    assert corpus.name == "pdfs"
    assert corpus.parent == cache.literature_dir()


def test_pdf_corpus_env_override(monkeypatch, tmp_path):
    monkeypatch.setenv("PDF_CORPUS_DIR", str(tmp_path))
    assert cache.pdf_corpus_dir() == tmp_path
