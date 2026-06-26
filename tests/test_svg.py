"""Unit tests for bio_toolkit.util.svg.

Pure logic (id cleaning, output path, input validation) is tested directly.
The bs4-backed load/highlight/save path is exercised only when bs4 is installed
(pytest.importorskip), since BeautifulSoup is an optional dep.
"""

import pytest

from bio_toolkit.util.svg import EnhancedSVGDiagram, DEFAULT_FILL_COLOUR, DEFAULT_STROKE_COLOUR


def _make(tmp_path, base_paths=None, elements=("nucleus",), gene="PAX6"):
    out_dir = tmp_path / "out"
    return EnhancedSVGDiagram(
        base_diagram="Animal_cells",
        elements_to_highlight=list(elements),
        gene_symbol=gene,
        svg_paths=base_paths or {},
        output_dir=str(out_dir),
    )


# --- pure logic ---

def test_clean_element_ids_strips_hyphens(tmp_path):
    d = _make(tmp_path, elements=["nucl-eus", "golgi", ""])
    assert d.elements_to_highlight == ["nucleus", "golgi"]


def test_output_path_and_vault_relative(tmp_path):
    d = _make(tmp_path, gene="OTX2")
    assert d.output_path.endswith("OTX2_Animal_cells.svg")
    assert d.vault_relative_path == "_assets/images/genes_svg/OTX2_Animal_cells.svg"
    # output dir is created on construction
    assert (tmp_path / "out").is_dir()


def test_custom_vault_relative_prefix(tmp_path):
    out_dir = tmp_path / "out"
    d = EnhancedSVGDiagram(
        base_diagram="Animal_cells",
        elements_to_highlight=["nucleus"],
        gene_symbol="X",
        svg_paths={},
        output_dir=str(out_dir),
        vault_relative_prefix="custom/path/",
    )
    assert d.vault_relative_path == "custom/path/X_Animal_cells.svg"


def test_default_colours(tmp_path):
    d = _make(tmp_path)
    assert d.fill_colour == DEFAULT_FILL_COLOUR
    assert d.stroke_colour == DEFAULT_STROKE_COLOUR


def test_validate_unknown_base_diagram(tmp_path):
    d = _make(tmp_path)  # empty svg_paths -> base_diagram_path is None
    ok, msg = d._validate_inputs()
    assert ok is False
    assert "Unknown base diagram" in msg


def test_validate_missing_file(tmp_path):
    d = _make(tmp_path, base_paths={"Animal_cells": str(tmp_path / "missing.svg")})
    ok, msg = d._validate_inputs()
    assert ok is False
    assert "not found" in msg


def test_validate_no_elements(tmp_path):
    svg = tmp_path / "base.svg"
    svg.write_text("<svg/>", encoding="utf-8")
    d = EnhancedSVGDiagram(
        base_diagram="Animal_cells",
        elements_to_highlight=[],
        gene_symbol="X",
        svg_paths={"Animal_cells": str(svg)},
        output_dir=str(tmp_path / "out"),
    )
    ok, msg = d._validate_inputs()
    assert ok is False
    assert "No elements" in msg


def test_validate_valid(tmp_path):
    svg = tmp_path / "base.svg"
    svg.write_text("<svg/>", encoding="utf-8")
    d = EnhancedSVGDiagram(
        base_diagram="Animal_cells",
        elements_to_highlight=["nucleus"],
        gene_symbol="X",
        svg_paths={"Animal_cells": str(svg)},
        output_dir=str(tmp_path / "out"),
    )
    ok, msg = d._validate_inputs()
    assert ok is True


def test_get_element_info(tmp_path):
    d = _make(tmp_path, gene="PAX6")
    info = d.get_element_info()
    assert info["gene_symbol"] == "PAX6"
    assert info["base_diagram"] == "Animal_cells"
    assert info["elements_to_highlight"] == ["nucleus"]


# --- bs4-backed full path (optional dep) ---

_SVG = """<svg xmlns="http://www.w3.org/2000/svg">
  <g id="nucleus">
    <text class="subcell_description">The nucleus</text>
    <path class=" coloured" fill="#000000" stroke="#000000" d="M0 0"/>
  </g>
  <g id="golgi">
    <rect x="1" y="1" width="2" height="2"/>
  </g>
</svg>
"""


def _write_base(tmp_path):
    svg = tmp_path / "base.svg"
    svg.write_text(_SVG, encoding="utf-8")
    return svg


def test_highlight_svg_full(tmp_path):
    pytest.importorskip("bs4")
    svg = _write_base(tmp_path)
    out_dir = tmp_path / "out"
    d = EnhancedSVGDiagram(
        base_diagram="Animal_cells",
        elements_to_highlight=["nucleus", "golgi"],
        gene_symbol="PAX6",
        svg_paths={"Animal_cells": str(svg)},
        output_dir=str(out_dir),
    )
    vault_path, status = d.highlight_svg()
    assert vault_path == "_assets/images/genes_svg/PAX6_Animal_cells.svg"
    assert "Successfully highlighted" in status
    # descriptions extracted
    assert d.get_descriptions() == ["The nucleus"]
    # output written and recoloured
    written = (out_dir / "PAX6_Animal_cells.svg").read_text(encoding="utf-8")
    assert DEFAULT_FILL_COLOUR in written
    assert DEFAULT_STROKE_COLOUR in written


def test_highlight_svg_no_match(tmp_path):
    pytest.importorskip("bs4")
    svg = _write_base(tmp_path)
    d = EnhancedSVGDiagram(
        base_diagram="Animal_cells",
        elements_to_highlight=["does_not_exist"],
        gene_symbol="X",
        svg_paths={"Animal_cells": str(svg)},
        output_dir=str(tmp_path / "out"),
    )
    vault_path, status = d.highlight_svg()
    assert vault_path == ""
    assert "No elements were highlighted" in status


def test_highlight_svg_validation_failure(tmp_path):
    # No bs4 needed: validation fails before load (unknown base diagram)
    d = _make(tmp_path)
    vault_path, status = d.highlight_svg()
    assert vault_path == ""
    assert "Validation failed" in status
