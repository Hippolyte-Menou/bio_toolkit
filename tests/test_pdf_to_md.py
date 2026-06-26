"""Unit tests for bio_toolkit.pdf.pdf_to_md.

Tests pure path/frontmatter logic and the two convert functions with the
heavyweight converters mocked. NO real PDF conversion, NO network.
"""

from datetime import date
from pathlib import Path

import pytest

from bio_toolkit.pdf import pdf_to_md


# --- frontmatter ---

def test_build_frontmatter_basic():
    fm = pdf_to_md.build_frontmatter("articles/x.pdf", "opendataloader")
    assert fm.startswith("---")
    assert fm.endswith("---")
    assert 'source-pdf: "articles/x.pdf"' in fm
    assert "converter: opendataloader" in fm
    assert f"converted: {date.today().isoformat()}" in fm
    assert "  - source/pdf-conversion" in fm
    # no topic line when topic omitted
    assert "topic/" not in fm


def test_build_frontmatter_with_topic():
    fm = pdf_to_md.build_frontmatter("a.pdf", "marker", topic="X-linked-RD")
    assert "  - topic/X-linked-RD" in fm


# --- path computation ---

def test_compute_output_path_mirrors_subfolders(tmp_path):
    pdfs_dir = tmp_path / "pdfs"
    out_dir = tmp_path / "out"
    pdf = pdfs_dir / "articles" / "2002_Munier.pdf"
    pdf.parent.mkdir(parents=True)
    pdf.write_bytes(b"%PDF-1.4")

    out = pdf_to_md.compute_output_path(pdf, pdfs_dir, out_dir)
    assert out == out_dir / "articles" / "2002_Munier.md"


def test_compute_output_path_outside_input_goes_flat(tmp_path):
    pdfs_dir = tmp_path / "pdfs"
    pdfs_dir.mkdir()
    out_dir = tmp_path / "out"
    elsewhere = tmp_path / "elsewhere" / "paper.pdf"
    elsewhere.parent.mkdir(parents=True)
    elsewhere.write_bytes(b"%PDF-1.4")

    out = pdf_to_md.compute_output_path(elsewhere, pdfs_dir, out_dir)
    assert out == out_dir / "paper.md"


def test_compute_source_relative_inside_uses_forward_slashes(tmp_path):
    pdfs_dir = tmp_path / "pdfs"
    pdf = pdfs_dir / "articles" / "p.pdf"
    pdf.parent.mkdir(parents=True)
    pdf.write_bytes(b"%PDF-1.4")
    rel = pdf_to_md.compute_source_relative(pdf, pdfs_dir)
    assert rel == "articles/p.pdf"


def test_compute_source_relative_outside_uses_name(tmp_path):
    pdfs_dir = tmp_path / "pdfs"
    pdfs_dir.mkdir()
    pdf = tmp_path / "other" / "p.pdf"
    pdf.parent.mkdir(parents=True)
    pdf.write_bytes(b"%PDF-1.4")
    assert pdf_to_md.compute_source_relative(pdf, pdfs_dir) == "p.pdf"


# --- collect_pdfs ---

def test_collect_pdfs_single_file(tmp_path):
    pdf = tmp_path / "x.pdf"
    pdf.write_bytes(b"%PDF-1.4")
    assert pdf_to_md.collect_pdfs(pdf) == [pdf]


def test_collect_pdfs_directory_recursive(tmp_path):
    (tmp_path / "a.pdf").write_bytes(b"%PDF")
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "b.pdf").write_bytes(b"%PDF")
    (tmp_path / "note.txt").write_text("not a pdf")
    found = pdf_to_md.collect_pdfs(tmp_path)
    assert {p.name for p in found} == {"a.pdf", "b.pdf"}


def test_collect_pdfs_invalid_returns_empty(tmp_path):
    assert pdf_to_md.collect_pdfs(tmp_path / "nope.pdf") == []


# --- _safe_copy ---

def test_safe_copy_no_change_returns_none(tmp_path):
    pdf = tmp_path / "plain-name.pdf"
    pdf.write_bytes(b"%PDF")
    assert pdf_to_md._safe_copy(pdf, str(tmp_path)) is None


def test_safe_copy_sanitizes_unicode_dash(tmp_path):
    # U+2013 EN DASH in the filename
    pdf = tmp_path / "a–b.pdf"
    pdf.write_bytes(b"%PDF")
    dest = tmp_path / "copies"
    dest.mkdir()
    out = pdf_to_md._safe_copy(pdf, str(dest))
    assert out is not None
    assert out.name == "a-b.pdf"
    assert out.exists()


# --- convert_single_opendataloader with the converter mocked ---

def test_convert_opendataloader_skips_existing(tmp_path, monkeypatch):
    pdfs_dir = tmp_path / "pdfs"
    out_dir = tmp_path / "out"
    pdf = pdfs_dir / "p.pdf"
    pdf.parent.mkdir(parents=True)
    pdf.write_bytes(b"%PDF")
    # pre-create output so it is skipped
    existing = out_dir / "p.md"
    existing.parent.mkdir(parents=True)
    existing.write_text("already")

    # The lazy `from opendataloader_pdf import convert` runs before the skip
    # check, so stub the module; convert must NOT be invoked on the skip path.
    import sys
    import types

    fake = types.ModuleType("opendataloader_pdf")

    def _should_not_run(*a, **k):
        raise AssertionError("convert should not be called when output exists")

    fake.convert = _should_not_run
    monkeypatch.setitem(sys.modules, "opendataloader_pdf", fake)

    res = pdf_to_md.convert_single_opendataloader(pdf, pdfs_dir, out_dir, force=False)
    assert res["skipped"] is True
    assert res["success"] is False


def test_convert_opendataloader_success_mocked(tmp_path, monkeypatch):
    pdfs_dir = tmp_path / "pdfs"
    out_dir = tmp_path / "out"
    pdf = pdfs_dir / "articles" / "p.pdf"
    pdf.parent.mkdir(parents=True)
    pdf.write_bytes(b"%PDF")

    # Fake opendataloader_pdf module: writes a markdown file into output_dir
    import sys
    import types

    fake = types.ModuleType("opendataloader_pdf")

    def fake_convert(input_path, output_dir, format, quiet):
        Path(output_dir, "p.md").write_text("# Body\n\nReal content here.", encoding="utf-8")

    fake.convert = fake_convert
    monkeypatch.setitem(sys.modules, "opendataloader_pdf", fake)

    res = pdf_to_md.convert_single_opendataloader(pdf, pdfs_dir, out_dir)
    assert res["success"] is True
    out_file = out_dir / "articles" / "p.md"
    assert out_file.is_file()
    content = out_file.read_text(encoding="utf-8")
    assert content.startswith("---")
    assert "converter: opendataloader" in content
    assert "Real content here." in content


def test_convert_opendataloader_json_wrapped(tmp_path, monkeypatch):
    pdfs_dir = tmp_path / "pdfs"
    out_dir = tmp_path / "out"
    pdf = pdfs_dir / "p.pdf"
    pdf.parent.mkdir(parents=True)
    pdf.write_bytes(b"%PDF")

    import json as _json
    import sys
    import types

    fake = types.ModuleType("opendataloader_pdf")

    def fake_convert(input_path, output_dir, format, quiet):
        payload = {"formats": {"markdown": {"content": "JSON-wrapped markdown body"}}}
        Path(output_dir, "p.json").write_text(_json.dumps(payload), encoding="utf-8")

    fake.convert = fake_convert
    monkeypatch.setitem(sys.modules, "opendataloader_pdf", fake)

    res = pdf_to_md.convert_single_opendataloader(pdf, pdfs_dir, out_dir)
    assert res["success"] is True
    content = (out_dir / "p.md").read_text(encoding="utf-8")
    assert "JSON-wrapped markdown body" in content


# --- convert_single_marker with injected (mock) converter callables ---

class _FakeImage:
    def __init__(self, data):
        self.data = data

    def save(self, path):
        Path(path).write_bytes(self.data)


def test_convert_marker_success_mocked(tmp_path):
    pdfs_dir = tmp_path / "pdfs"
    out_dir = tmp_path / "out"
    pdf = pdfs_dir / "p.pdf"
    pdf.parent.mkdir(parents=True)
    pdf.write_bytes(b"%PDF")

    def fake_converter(path):
        return {"rendered": path}

    def fake_text_from_rendered(rendered):
        return "Marker body text", {}, {"fig1.png": _FakeImage(b"PNGDATA")}

    res = pdf_to_md.convert_single_marker(
        pdf, pdfs_dir, out_dir, fake_converter, fake_text_from_rendered
    )
    assert res["success"] is True
    out_file = out_dir / "p.md"
    content = out_file.read_text(encoding="utf-8")
    assert "converter: marker" in content
    assert "Marker body text" in content
    # image written
    assert (out_dir / "p_images" / "fig1.png").read_bytes() == b"PNGDATA"


def test_convert_marker_disable_images(tmp_path):
    pdfs_dir = tmp_path / "pdfs"
    out_dir = tmp_path / "out"
    pdf = pdfs_dir / "p.pdf"
    pdf.parent.mkdir(parents=True)
    pdf.write_bytes(b"%PDF")

    res = pdf_to_md.convert_single_marker(
        pdf,
        pdfs_dir,
        out_dir,
        lambda p: None,
        lambda r: ("text", {}, {"fig.png": _FakeImage(b"X")}),
        disable_images=True,
    )
    assert res["success"] is True
    assert not (out_dir / "p_images").exists()


def test_convert_marker_failure_captured(tmp_path):
    pdfs_dir = tmp_path / "pdfs"
    out_dir = tmp_path / "out"
    pdf = pdfs_dir / "p.pdf"
    pdf.parent.mkdir(parents=True)
    pdf.write_bytes(b"%PDF")

    def boom(path):
        raise RuntimeError("model exploded")

    res = pdf_to_md.convert_single_marker(pdf, pdfs_dir, out_dir, boom, lambda r: r)
    assert res["success"] is False
    assert "model exploded" in res["error"]
