"""Unit tests for bio_toolkit.pdf.smart_reconvert.

Tests pure scanning/assessment/YAML-rewrite logic and mocks the Marker
subprocess. NO real reconversion, NO network.
"""

import json

import pytest

from bio_toolkit.pdf import smart_reconvert as sr
from bio_toolkit.pdf.smart_reconvert import ReconvertPaths


# --- ReconvertPaths ---

def test_from_vault_root_layout(tmp_path):
    paths = ReconvertPaths.from_vault_root(tmp_path)
    assert paths.references_dir == (tmp_path / "R - References" / "Automatically Imported").resolve() \
        or paths.references_dir.name == "Automatically Imported"
    assert paths.pdfs_dir.name == "pdfs"
    assert paths.pdfs_md_dir.name == "pdfs-md"
    assert paths.scratch_dir.name == "data"


def _make_paths(tmp_path):
    """Build a ReconvertPaths with all dirs created."""
    paths = ReconvertPaths.from_vault_root(tmp_path)
    for d in (paths.references_dir, paths.pdfs_dir, paths.pdfs_md_dir, paths.scratch_dir):
        d.mkdir(parents=True, exist_ok=True)
    return paths


# --- assess_markdown_quality ---

def test_assess_quality_missing_file_is_abstract_only(tmp_path):
    assert sr.assess_markdown_quality(None) == "abstract-only"
    assert sr.assess_markdown_quality(tmp_path / "nope.md") == "abstract-only"


def test_assess_quality_short_is_abstract_only(tmp_path):
    md = tmp_path / "a.md"
    md.write_text("---\ntitle: x\n---\n\nshort", encoding="utf-8")
    assert sr.assess_markdown_quality(md) == "abstract-only"


def test_assess_quality_medium_for_substantive_text(tmp_path):
    md = tmp_path / "b.md"
    body = "Lorem ipsum dolor sit amet. " * 60  # > 1000 substantive chars
    md.write_text(f"---\ntitle: x\n---\n\n{body}", encoding="utf-8")
    assert sr.assess_markdown_quality(md) == "medium"


def test_assess_quality_low_for_medium_length(tmp_path):
    md = tmp_path / "c.md"
    body = "word " * 80  # ~400 chars: >=200 but <500 -> low
    md.write_text(f"---\nt: x\n---\n\n{body}", encoding="utf-8")
    assert sr.assess_markdown_quality(md) == "low"


def test_assess_quality_low_for_garbled_non_ascii(tmp_path):
    md = tmp_path / "d.md"
    # >500 substantive chars but >15% weird non-ascii (not French accents)
    body = "□" * 400 + "the quick brown fox jumps over the lazy dog " * 5
    md.write_text(f"---\nt: x\n---\n\n{body}", encoding="utf-8")
    assert sr.assess_markdown_quality(md) == "low"


# --- find_pdf_for_citekey ---

def test_find_pdf_direct_match(tmp_path):
    paths = _make_paths(tmp_path)
    articles = paths.pdfs_dir / "articles"
    articles.mkdir()
    pdf = articles / "Munier2002.pdf"
    pdf.write_bytes(b"%PDF")
    found = sr.find_pdf_for_citekey("Munier2002", paths)
    assert found == pdf


def test_find_pdf_glob_fallback(tmp_path):
    paths = _make_paths(tmp_path)
    sub = paths.pdfs_dir / "misc"
    sub.mkdir()
    pdf = sub / "2010_Smith_paper.pdf"
    pdf.write_bytes(b"%PDF")
    found = sr.find_pdf_for_citekey("Smith", paths)
    assert found == pdf


def test_find_pdf_none_for_empty_citekey(tmp_path):
    paths = _make_paths(tmp_path)
    assert sr.find_pdf_for_citekey("", paths) is None


# --- find_reconvert_candidates_from_notes ---

def test_find_candidates_from_notes(tmp_path):
    paths = _make_paths(tmp_path)
    # a candidate: low quality + opendataloader
    (paths.references_dir / "art1.md").write_text(
        "---\nsource-quality: low\nconverter: opendataloader\ncitekey: Munier2002\n---\nbody",
        encoding="utf-8",
    )
    # not a candidate: high quality
    (paths.references_dir / "art2.md").write_text(
        "---\nsource-quality: high\nconverter: opendataloader\ncitekey: X\n---\nbody",
        encoding="utf-8",
    )
    # not a candidate: marker converter
    (paths.references_dir / "art3.md").write_text(
        "---\nsource-quality: low\nconverter: marker\ncitekey: Y\n---\nbody",
        encoding="utf-8",
    )
    cands = sr.find_reconvert_candidates_from_notes(paths)
    assert len(cands) == 1
    assert cands[0]["citekey"] == "Munier2002"
    assert cands[0]["source"] == "note-scan"


def test_find_candidates_missing_references_dir(tmp_path, capsys):
    paths = ReconvertPaths.from_vault_root(tmp_path)  # dirs not created
    cands = sr.find_reconvert_candidates_from_notes(paths)
    assert cands == []


# --- find_reconvert_candidates_from_scratch ---

def test_find_candidates_from_scratch(tmp_path):
    paths = _make_paths(tmp_path)
    scratch = paths.scratch_dir / "scratch_extract_001.json"
    scratch.write_text(
        json.dumps(
            {
                "reconvert": True,
                "citekey": "Z2020",
                "article_name": "Z paper",
                "article_path": "R - References/z.md",
                "yaml_updates": {"source-quality": "low", "extraction-status": "extracted"},
            }
        ),
        encoding="utf-8",
    )
    # one without reconvert flag (ignored)
    (paths.scratch_dir / "scratch_extract_002.json").write_text(
        json.dumps({"reconvert": False, "citekey": "Q"}), encoding="utf-8"
    )
    cands = sr.find_reconvert_candidates_from_scratch(paths)
    assert len(cands) == 1
    assert cands[0]["citekey"] == "Z2020"
    assert cands[0]["source"] == "scratch-file"


# --- find_unassessed_articles ---

def test_find_unassessed_blank_quality(tmp_path):
    paths = _make_paths(tmp_path)
    (paths.references_dir / "a.md").write_text(
        "---\nsource-quality:\nconverter: opendataloader\ncitekey: A\n---\nbody",
        encoding="utf-8",
    )
    # has quality set -> not unassessed
    (paths.references_dir / "b.md").write_text(
        "---\nsource-quality: high\nconverter: opendataloader\ncitekey: B\n---\nbody",
        encoding="utf-8",
    )
    un = sr.find_unassessed_articles(paths)
    assert len(un) == 1
    assert un[0]["citekey"] == "A"


# --- YAML rewrite helpers ---

def test_write_source_quality(tmp_path):
    note = tmp_path / "note.md"
    note.write_text("---\nsource-quality: low\nconverter: opendataloader\n---\nbody", encoding="utf-8")
    assert sr.write_source_quality(str(note), "medium") is True
    assert "source-quality: medium" in note.read_text(encoding="utf-8")


def test_write_source_quality_missing_field(tmp_path):
    note = tmp_path / "note.md"
    note.write_text("---\nconverter: opendataloader\n---\nbody", encoding="utf-8")
    assert sr.write_source_quality(str(note), "medium") is False


def test_reset_extraction_status(tmp_path):
    note = tmp_path / "note.md"
    note.write_text(
        "---\nsource-quality: low\nconverter: opendataloader\nextraction-status: extracted\n---\nbody",
        encoding="utf-8",
    )
    assert sr.reset_extraction_status(str(note)) is True
    out = note.read_text(encoding="utf-8")
    assert "extraction-status: pending" in out
    assert "converter: marker" in out
    assert "source-quality:\n" in out  # blanked


# --- reconvert_pdf (subprocess mocked) ---

def test_reconvert_pdf_success(tmp_path, monkeypatch):
    captured = {}

    class _Result:
        returncode = 0
        stderr = ""

    def fake_run(cmd, capture_output, text, timeout):
        captured["cmd"] = cmd
        return _Result()

    monkeypatch.setattr(sr.subprocess, "run", fake_run)
    ok = sr.reconvert_pdf("x.pdf", tmp_path / "pdfs", tmp_path / "out")
    assert ok is True
    # invokes the ported module with --marker --force and the dir args
    assert "-m" in captured["cmd"]
    assert "bio_toolkit.pdf.pdf_to_md" in captured["cmd"]
    assert "--marker" in captured["cmd"]
    assert "--force" in captured["cmd"]
    assert "--pdfs-dir" in captured["cmd"]
    assert "--output-dir" in captured["cmd"]


def test_reconvert_pdf_failure(tmp_path, monkeypatch):
    class _Result:
        returncode = 1
        stderr = "boom"

    monkeypatch.setattr(sr.subprocess, "run", lambda *a, **k: _Result())
    assert sr.reconvert_pdf("x.pdf", tmp_path / "pdfs", tmp_path / "out") is False


def test_reconvert_pdf_timeout(tmp_path, monkeypatch):
    def fake_run(*a, **k):
        raise sr.subprocess.TimeoutExpired(cmd="x", timeout=1800)

    monkeypatch.setattr(sr.subprocess, "run", fake_run)
    assert sr.reconvert_pdf("x.pdf", tmp_path / "pdfs", tmp_path / "out") is False
