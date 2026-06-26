"""Unit tests for bio_toolkit.util.templating.

Pure data-helper logic is tested directly. Jinja2 rendering is exercised only
when jinja2 is installed (pytest.importorskip), since it is an optional dep.
"""

import json

import pytest

from bio_toolkit.util import templating as t


# --- JSON loading ---

def test_load_json_data_missing_returns_empty(tmp_path):
    assert t.load_json_data("nope.json", str(tmp_path)) == {}


def test_load_json_data_reads_file(tmp_path):
    (tmp_path / "d.json").write_text(json.dumps({"a": 1}), encoding="utf-8")
    assert t.load_json_data("d.json", str(tmp_path)) == {"a": 1}


def test_get_disease_lookup_cached(tmp_path):
    (tmp_path / "disease_lookup.json").write_text(
        json.dumps({"RP": {"group_name": "Retinitis pigmentosa"}}), encoding="utf-8"
    )
    lookup = t.get_disease_lookup(str(tmp_path))
    assert lookup["RP"]["group_name"] == "Retinitis pigmentosa"


# --- disease formatting ---

def test_format_disease_with_lookup_group_name():
    lookup = {"RP19": {"group_name": "Retinitis pigmentosa", "full_name": "Retinitis pigmentosa 19"}}
    assert t.format_disease_with_lookup("RP19", lookup) == "Retinitis pigmentosa"


def test_format_disease_with_lookup_falls_back_to_full_name():
    lookup = {"X": {"full_name": "Full X"}}
    assert t.format_disease_with_lookup("X", lookup) == "Full X"


def test_format_disease_with_lookup_unknown_returns_acronym():
    assert t.format_disease_with_lookup("ZZZ", {}) == "ZZZ"


def test_get_pathology_tags_dedup():
    lookup = {
        "A": {"pathology_tag": "eye/retina"},
        "B": {"pathology_tag": "eye/retina"},
        "C": {"pathology_tag": "eye/cornea"},
    }
    tags = t.get_pathology_tags_for_diseases(["A", "B", "C"], lookup)
    assert tags == ["eye/retina", "eye/cornea"]


# --- reverse index ---

def test_build_gene_to_pathologies_index_skips_headers():
    mapping = {
        "Cat": {"fr_name": "Cat", "is_category_header": True, "genes": ["X"]},
        "Maladie A": {
            "fr_name": "Maladie A",
            "en_name": "Disease A",
            "pathology_tag": "eye/x",
            "genes": ["PAX6", "OTX2"],
        },
    }
    index = t.build_gene_to_pathologies_index(mapping)
    assert "X" not in index  # header skipped
    assert index["PAX6"][0]["en_name"] == "Disease A"
    assert index["OTX2"][0]["pathology_tag"] == "eye/x"


# --- alias parsing ---

def test_parse_aliases_string():
    assert t.parse_aliases("A | B |  | C") == ["A", "B", "C"]


def test_parse_aliases_list():
    assert t.parse_aliases(["A", "B"]) == ["A", "B"]


def test_parse_aliases_other():
    assert t.parse_aliases(None) == []


# --- resolve_disease_metadata ---

def test_resolve_disease_metadata():
    lookup = {"RP19": {"group_name": "Retinitis pigmentosa", "pathology_tag": "eye/retina"}}
    diseases = [{"disease_acronym": "RP19"}]
    keywords = ["Disease variant", "Coloboma"]
    formatted, acronyms, tags = t.resolve_disease_metadata(diseases, keywords, lookup)
    assert formatted == ["Retinitis pigmentosa", "Coloboma"]  # "Disease variant" filtered
    assert acronyms == ["RP19"]
    assert tags == ["eye/retina"]


def test_resolve_disease_metadata_default_lookup():
    formatted, acronyms, tags = t.resolve_disease_metadata([{"disease_acronym": "ZZ"}], [])
    assert formatted == ["ZZ"]  # unknown acronym passes through
    assert tags == []


# --- Jinja2 rendering (optional dep) ---

def test_render_string_trivial():
    pytest.importorskip("jinja2")
    out = t.render_string("Hello {{ name }}!", {"name": "PAX6"})
    assert out == "Hello PAX6!"


def test_render_string_kwargs_and_loop():
    pytest.importorskip("jinja2")
    tmpl = "{% for g in genes %}{{ g }};{% endfor %}"
    out = t.render_string(tmpl, genes=["A", "B"])
    assert out == "A;B;"


def test_render_file_trivial(tmp_path):
    pytest.importorskip("jinja2")
    tpl = tmp_path / "t.j2"
    tpl.write_text("Gene: {{ symbol }}\n", encoding="utf-8")
    out = t.render_file(str(tpl), {"symbol": "OTX2"})
    assert out == "Gene: OTX2\n"
