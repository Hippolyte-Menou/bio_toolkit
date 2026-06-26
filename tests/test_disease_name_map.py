"""Unit tests for bio_toolkit.refdata.disease_name_map. No network."""

import json

from bio_toolkit.refdata import disease_name_map as dnm


# --- pure helpers ---

def test_normalize_fr_strips_diacritics():
    assert dnm.normalize_fr("Rétinite") == "Retinite"
    assert dnm.normalize_fr("Œdème maculaire") == dnm.normalize_fr("Œdeme maculaire")
    assert dnm.normalize_fr("Albinisme") == "Albinisme"


def test_build_tag_gene_index_aggregates():
    tag_mapping = {
        "Aniridia": {"pathology_tag": "anterior/aniridia", "genes": ["PAX6"], "omim": "106210"},
        "Aniridia variant": {"pathology_tag": "anterior/aniridia", "genes": ["ELP4", "PAX6"], "omim": ""},
        "No tag group": {"pathology_tag": "", "genes": ["XYZ"], "omim": ""},
    }
    gene_index, omim_index = dnm.build_tag_gene_index(tag_mapping)
    assert gene_index["anterior/aniridia"] == ["ELP4", "PAX6"]  # union, sorted
    assert omim_index["anterior/aniridia"] == "106210"          # first-seen
    assert "" not in gene_index  # tagless group skipped


# --- MOC parsing (tmp fixtures) ---

MOC_TEXT = """\
# Pathologies

## Albinismes

### [[Albinisme oculocutané]]
- bullet one
- bullet two

### [[Albinisme oculaire]]
- ocular only

## Dystrophies rétiniennes

### [[Rétinite pigmentaire]]
- rod-cone
"""


def _write_moc(tmp_path):
    p = tmp_path / "moc.md"
    p.write_text(MOC_TEXT, encoding="utf-8")
    return p


def test_parse_moc_categories(tmp_path):
    moc = _write_moc(tmp_path)
    entries = dnm.parse_moc(moc)
    assert ("Albinisme oculocutané", "Albinismes") in entries
    assert ("Rétinite pigmentaire", "Dystrophies rétiniennes") in entries
    assert len(entries) == 3


def test_parse_moc_descriptions(tmp_path):
    moc = _write_moc(tmp_path)
    desc = dnm.parse_moc_descriptions(moc)
    assert desc["Albinisme oculocutané"] == ["bullet one", "bullet two"]
    assert desc["Rétinite pigmentaire"] == ["rod-cone"]


# --- end-to-end build_mapping ---

def test_build_mapping_full(tmp_path):
    moc = _write_moc(tmp_path)

    taxonomy = tmp_path / "taxonomy.yaml"
    taxonomy.write_text(
        "disease_name_mapping:\n"
        "  - fr_name: 'Albinisme oculocutané'\n"
        "    en_name: 'Oculocutaneous albinism'\n"
        "    pathology_tag: 'albinism/oca'\n"
        "    genes: ['TYR']\n"
        "    omim_ids: ['203100']\n"
        "  - fr_name: 'Albinisme oculaire'\n"      # no genes/omim -> tag fallback
        "    en_name: 'Ocular albinism'\n"
        "    pathology_tag: 'albinism/oa'\n"
        "  - fr_name: 'Dystrophies rétiniennes'\n"  # category header (won't match a ### name)
        "    en_name: null\n"
        "    is_category_header: true\n",
        encoding="utf-8",
    )

    tag_mapping = tmp_path / "pathology_tag_mapping.json"
    tag_mapping.write_text(
        json.dumps({
            "Ocular albinism group": {
                "pathology_tag": "albinism/oa",
                "genes": ["GPR143"],
                "omim": "300500",
            }
        }),
        encoding="utf-8",
    )

    mapping, unmatched, matched = dnm.build_mapping(moc, tag_mapping, taxonomy)

    # OCA: direct YAML genes/omim
    oca = mapping["Albinisme oculocutané"]
    assert oca["en_name"] == "Oculocutaneous albinism"
    assert oca["genes"] == ["TYR"]
    assert oca["omim"] == "203100"
    assert oca["moc_descriptions"] == ["bullet one", "bullet two"]
    assert oca["is_category_header"] is False

    # OA: tag-based fallback for genes and omim
    oa = mapping["Albinisme oculaire"]
    assert oa["genes"] == ["GPR143"]
    assert oa["omim"] == "300500"

    # "Rétinite pigmentaire" is in the MOC but not in taxonomy -> unmatched
    assert ("Rétinite pigmentaire", "Dystrophies rétiniennes") in unmatched

    # two non-header diseases matched
    assert matched == 2
