"""
FR<->EN disease-name mapping for ophthalmology pathology notes.

Ported from the vault's gene-generation/build_disease_name_mapping.py. Pure data
transforms (no HTTP): parse the French Pathology MOC, look each note up in
taxonomy.yaml, and fall back to tag-based gene aggregation from
pathology_tag_mapping.json. This module RETURNS the parsed mapping; JSON/review
file writing stays in the original script.

All input paths are arguments (defaulting to the original vault layout) so the
module does not hard-depend on the vault being present.
"""

from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import yaml

# ── Default vault paths (ported from the original layout) ──────────────────────

_VAULT_CODE_DIR = Path(
    "C:/Users/hippo/OneDrive/Documents/Genetics/Ophtalmogenetics/_assets/code"
)
_GENE_GEN_DATA = _VAULT_CODE_DIR / "gene-generation" / "data"
_VAULT_DIR = _VAULT_CODE_DIR.parent.parent

DEFAULT_TAXONOMY_YAML = _GENE_GEN_DATA / "taxonomy.yaml"
DEFAULT_TAG_MAPPING_JSON = _GENE_GEN_DATA / "pathology_tag_mapping.json"
DEFAULT_MOC_PATH = _VAULT_DIR / "F - Pathologies" / "Pathologies ophtalmologiques - MOC.md"


def normalize_fr(name: str) -> str:
    """Normalize a French name by stripping diacritics (for lookup keys)."""
    nfkd = unicodedata.normalize("NFKD", name)
    return "".join(c for c in nfkd if not unicodedata.combining(c))


def load_yaml_mapping(taxonomy_path: Path = DEFAULT_TAXONOMY_YAML) -> Dict[str, dict]:
    """
    Load the disease_name_mapping section from taxonomy.yaml.

    Returns a dict keyed by normalize_fr(fr_name) -> entry with keys
    en_name, pathology_tag, genes, omim_ids, is_category_header.
    """
    with open(taxonomy_path, encoding="utf-8") as f:
        data = yaml.safe_load(f)

    entries = (data or {}).get("disease_name_mapping", [])

    lookup: Dict[str, dict] = {}
    for entry in entries:
        key = normalize_fr(entry["fr_name"])
        lookup[key] = {
            "en_name": entry.get("en_name"),
            "pathology_tag": entry.get("pathology_tag", ""),
            "genes": entry.get("genes", []),
            "omim_ids": entry.get("omim_ids", []),
            "is_category_header": entry.get("is_category_header", False),
        }
    return lookup


def parse_moc(moc_path: Path) -> List[Tuple[str, str]]:
    """
    Parse the pathology MOC to extract (fr_name, category) tuples.

    Categories come from '## ' headings; pathology names from '### [[name]]'
    wikilink headings. Duplicate names are kept once (first occurrence).
    """
    content = Path(moc_path).read_text(encoding="utf-8")
    current_category = ""
    entries: List[Tuple[str, str]] = []
    seen = set()

    for line in content.split("\n"):
        if line.startswith("## ") and not line.startswith("### "):
            current_category = line[3:].strip()
        m = re.match(r"### \[\[(.+?)\]\]", line)
        if m:
            name = m.group(1)
            if name not in seen:
                seen.add(name)
                entries.append((name, current_category))

    return entries


def parse_moc_descriptions(moc_path: Path) -> Dict[str, List[str]]:
    """
    Parse the MOC to collect the bullet-point descriptions under each pathology.

    Returns {fr_name: [bullet, ...]}.
    """
    content = Path(moc_path).read_text(encoding="utf-8")
    descriptions: Dict[str, List[str]] = {}
    current_name: Optional[str] = None
    current_bullets: List[str] = []

    for line in content.split("\n"):
        m = re.match(r"### \[\[(.+?)\]\]", line)
        if m:
            if current_name and current_bullets:
                descriptions[current_name] = current_bullets
            current_name = m.group(1)
            current_bullets = []
        elif current_name and line.startswith("- "):
            current_bullets.append(line[2:].strip())
        elif current_name and line.startswith("## "):
            if current_name and current_bullets:
                descriptions[current_name] = current_bullets
            current_name = None
            current_bullets = []

    if current_name and current_bullets:
        descriptions[current_name] = current_bullets

    return descriptions


def build_tag_gene_index(
    tag_mapping: dict,
) -> Tuple[Dict[str, List[str]], Dict[str, str]]:
    """
    Aggregate genes (and one OMIM) per pathology tag across all disease groups.

    Returns (tag -> sorted gene list, tag -> first-seen OMIM string).
    """
    tag_to_genes: Dict[str, set] = {}
    tag_to_omim: Dict[str, str] = {}

    for group_name, info in tag_mapping.items():
        tag = info.get("pathology_tag", "")
        if not tag:
            continue
        if tag not in tag_to_genes:
            tag_to_genes[tag] = set()
            tag_to_omim[tag] = info.get("omim", "")
        for g in info.get("genes", []):
            tag_to_genes[tag].add(g)

    return (
        {tag: sorted(genes) for tag, genes in tag_to_genes.items()},
        tag_to_omim,
    )


def build_mapping(
    moc_path: Path = DEFAULT_MOC_PATH,
    tag_mapping_path: Path = DEFAULT_TAG_MAPPING_JSON,
    taxonomy_path: Path = DEFAULT_TAXONOMY_YAML,
) -> Tuple[Dict[str, dict], List[Tuple[str, str]], int]:
    """
    Build the FR->EN disease-name mapping.

    Returns (mapping, unmatched_moc, matched_count):
      - mapping: keyed by the French MOC name, each value carrying fr_name,
        en_name, pathology_tag, genes, omim, moc_category, moc_descriptions,
        in_moc, is_category_header.
      - unmatched_moc: [(fr_name, category), ...] not found in taxonomy.yaml.
      - matched_count: number of non-header disease entries matched.
    """
    yaml_lookup = load_yaml_mapping(taxonomy_path)

    with Path(tag_mapping_path).open(encoding="utf-8") as f:
        tag_mapping = json.load(f)

    tag_gene_index, tag_omim_index = build_tag_gene_index(tag_mapping)

    moc_entries = parse_moc(moc_path)
    moc_descriptions = parse_moc_descriptions(moc_path)

    mapping: Dict[str, dict] = {}
    unmatched_moc: List[Tuple[str, str]] = []
    matched_count = 0

    for fr_name_original, category in moc_entries:
        fr_name_normalized = normalize_fr(fr_name_original)
        yaml_entry = yaml_lookup.get(fr_name_normalized)

        if yaml_entry is None:
            unmatched_moc.append((fr_name_original, category))
            continue

        if yaml_entry["is_category_header"]:
            mapping[fr_name_original] = {
                "fr_name": fr_name_original,
                "en_name": None,
                "pathology_tag": "",
                "genes": [],
                "omim": "",
                "moc_category": category,
                "moc_descriptions": moc_descriptions.get(fr_name_original, []),
                "in_moc": True,
                "is_category_header": True,
            }
            continue

        en_name = yaml_entry["en_name"]
        pathology_tag = yaml_entry["pathology_tag"]
        genes = yaml_entry["genes"]
        omim_ids = yaml_entry.get("omim_ids", [])
        omim = omim_ids[0] if omim_ids else ""

        if not genes and pathology_tag and pathology_tag in tag_gene_index:
            genes = tag_gene_index[pathology_tag]

        if not omim and pathology_tag and pathology_tag in tag_omim_index:
            omim = tag_omim_index[pathology_tag]

        mapping[fr_name_original] = {
            "fr_name": fr_name_original,
            "en_name": en_name,
            "pathology_tag": pathology_tag,
            "genes": genes,
            "omim": omim,
            "moc_category": category,
            "moc_descriptions": moc_descriptions.get(fr_name_original, []),
            "in_moc": True,
            "is_category_header": False,
        }
        matched_count += 1

    return mapping, unmatched_moc, matched_count
