"""
Templating utilities: Jinja2 rendering + gene-note data helpers.

Ported from the vault's ``gene-generation/template_engine.py``. That module is an
f-string-based markdown builder tightly coupled to ``markdown_formatter`` and the
vault's ``settings`` (both outside this package and carrying hard-coded vault
paths). The reusable, dependency-light pieces are ported here:

* ``render_string`` / ``render_file`` -- generic Jinja2 rendering. ``jinja2`` is
  imported lazily inside these functions so this module imports cleanly without
  Jinja2 installed (it is only needed when you actually render).
* JSON data loading (``load_json_data``) and the disease lookup / formatting /
  reverse-index helpers, with the data directory passed in as an argument rather
  than hard-coded to the vault's ``gene-generation/data``.

Heavy data-builder methods that depended on ``markdown_formatter`` were not
ported (they belong with the gene-generation app, not the shared toolkit).
"""

import functools
import json
import os
from typing import Dict, List, Optional


# -- Generic Jinja2 rendering -------------------------------------------------

def _make_environment(searchpath: Optional[str] = None):
    """Build a Jinja2 Environment. ``jinja2`` is imported lazily.

    A FileSystemLoader is attached when *searchpath* is given so templates can
    use ``{% include %}`` / ``{% extends %}``.
    """
    from jinja2 import Environment, FileSystemLoader, select_autoescape

    loader = FileSystemLoader(searchpath) if searchpath else None
    return Environment(
        loader=loader,
        autoescape=select_autoescape(enabled_extensions=("html", "xml")),
        trim_blocks=True,
        lstrip_blocks=True,
        keep_trailing_newline=True,
    )


def render_string(template_str: str, context: Optional[Dict] = None, **kwargs) -> str:
    """Render a Jinja2 template given as a string with *context* (+ kwargs).

    ``jinja2`` is imported lazily; calling this without Jinja2 installed raises
    ImportError, but merely importing this module does not.
    """
    ctx = dict(context or {})
    ctx.update(kwargs)
    env = _make_environment()
    return env.from_string(template_str).render(**ctx)


def render_file(
    template_path: str, context: Optional[Dict] = None, **kwargs
) -> str:
    """Render a Jinja2 template file at *template_path* with *context* (+ kwargs).

    The template's own directory is added to the loader search path so relative
    ``{% include %}`` / ``{% extends %}`` resolve.
    """
    ctx = dict(context or {})
    ctx.update(kwargs)
    searchpath = os.path.dirname(os.path.abspath(template_path)) or "."
    name = os.path.basename(template_path)
    env = _make_environment(searchpath)
    return env.get_template(name).render(**ctx)


# -- JSON data loading --------------------------------------------------------

def load_json_data(filename: str, data_dir: str) -> Dict:
    """Load a JSON file from *data_dir*. Returns {} on missing file.

    Ported from ``template_engine._load_json_data``; the data directory was
    hard-coded to the script's ``data/`` subfolder there, and is an argument here.
    """
    try:
        with open(os.path.join(data_dir, filename), "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return {}


@functools.cache
def _load_json_cached(filename: str, data_dir: str) -> Dict:
    """Cache wrapper keyed on (filename, data_dir)."""
    return load_json_data(filename, data_dir)


def get_disease_lookup(data_dir: str) -> Dict[str, Dict]:
    """Get cached disease lookup table (acronym -> disease info with pathology tags)."""
    return _load_json_cached("disease_lookup.json", data_dir)


def get_disease_name_mapping(data_dir: str) -> Dict[str, Dict]:
    """Get cached disease name mapping (FR name -> disease info)."""
    return _load_json_cached("disease_name_mapping.json", data_dir)


# -- Disease formatting / lookup helpers --------------------------------------

def format_disease_with_lookup(acronym: str, lookup: Dict[str, Dict]) -> str:
    """Format a disease acronym using the lookup table.

    Uses group_name (without trailing numbers) to avoid numbered variants
    like 'Retinitis pigmentosa 19' returns just 'Retinitis pigmentosa'.
    """
    entry = lookup.get(acronym)
    if entry:
        name = entry.get("group_name") or entry.get("full_name")
        if name:
            return name
    return acronym


def get_pathology_tags_for_diseases(
    disease_acronyms: List[str], lookup: Dict[str, Dict]
) -> List[str]:
    """Resolve pathology tags for a list of disease acronyms.

    Returns deduplicated list of pathology tag paths (without #).
    """
    tags = []
    seen = set()
    for acronym in disease_acronyms:
        entry = lookup.get(acronym)
        if entry and entry.get("pathology_tag"):
            tag = entry["pathology_tag"]
            if tag not in seen:
                seen.add(tag)
                tags.append(tag)
    return tags


def build_gene_to_pathologies_index(mapping: Dict[str, Dict]) -> Dict[str, List[Dict]]:
    """Build reverse index: gene symbol -> list of pathology entries.

    Each entry in the list is a dict with fr_name, en_name, pathology_tag.
    Only includes disease entries (not category headers).
    """
    index: Dict[str, List[Dict]] = {}
    for fr_name, info in mapping.items():
        if info.get("is_category_header"):
            continue
        for gene in info.get("genes", []):
            if gene not in index:
                index[gene] = []
            index[gene].append(
                {
                    "fr_name": info["fr_name"],
                    "en_name": info.get("en_name", ""),
                    "pathology_tag": info.get("pathology_tag", ""),
                }
            )
    return index


def get_gene_to_pathologies(data_dir: str) -> Dict[str, List[Dict]]:
    """Get gene -> pathologies reverse index, built from the name mapping."""
    return build_gene_to_pathologies_index(get_disease_name_mapping(data_dir))


def parse_aliases(aliases) -> list:
    """Parse gene aliases from string or list format into a clean list."""
    if isinstance(aliases, str):
        return [a.strip() for a in aliases.split("|") if a.strip()]
    return aliases if isinstance(aliases, list) else []


def resolve_disease_metadata(
    diseases: list, disease_keywords: list, lookup: Optional[Dict] = None
):
    """Resolve formatted disease names and pathology tags from disease dicts.

    Returns (formatted_diseases, disease_acronyms, pathology_tags).

    *lookup* must be provided (or default to {}); the original defaulted it to
    the vault's cached lookup, which required the hard-coded data directory.
    """
    if lookup is None:
        lookup = {}

    disease_acronyms = [
        d.get("disease_acronym", "") for d in diseases if d.get("disease_acronym")
    ]
    keywords = [kw for kw in disease_keywords if kw != "Disease variant"]

    formatted_diseases = []
    seen = set()
    for acronym in disease_acronyms:
        formatted = format_disease_with_lookup(acronym, lookup)
        if formatted not in seen:
            seen.add(formatted)
            formatted_diseases.append(formatted)
    for kw in keywords:
        if kw not in seen:
            seen.add(kw)
            formatted_diseases.append(kw)

    pathology_tags = get_pathology_tags_for_diseases(disease_acronyms, lookup)
    return formatted_diseases, disease_acronyms, pathology_tags
