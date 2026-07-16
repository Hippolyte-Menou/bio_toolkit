"""Deterministic entity normalization for the fact store.

Genes -> approved HGNC symbol (bio_toolkit.clients.hgnc.resolve_symbol).
Diseases -> disease-taxonomy pathology-tag slug (bio_toolkit.refdata.disease_taxonomy).
Everything else -> a controlled-vocab slug.

Returns {'entity_type','entity_id','entity_label','confident'}. Unresolved entities
come back with entity_id=None and confident=False so the caller can flag the fact
for review rather than dropping it. Normalization is done HERE (Python), never by
the extractor LLM.
"""
from __future__ import annotations

import functools
import re

from bio_toolkit.clients.hgnc import resolve_symbol

_slug_re = re.compile(r"[^a-z0-9]+")


def slugify(name: str) -> str:
    return _slug_re.sub("-", (name or "").strip().lower()).strip("-")


@functools.lru_cache(maxsize=4096)
def _gene(name: str) -> str | None:
    try:
        return resolve_symbol(name)
    except Exception:
        return None


@functools.lru_cache(maxsize=1)
def _disease_rules():
    try:
        from bio_toolkit.refdata.disease_taxonomy import load_pathology_rules
        return load_pathology_rules()
    except Exception:
        return []


def normalize_entity(entity_type: str, name: str) -> dict:
    """Map one raw entity mention to a normalized record."""
    etype = (entity_type or "").strip().lower()
    label = (name or "").strip()
    if etype == "gene":
        sym = _gene(label)
        return {"entity_type": "gene", "entity_id": sym, "entity_label": label,
                "confident": sym is not None}
    if etype in ("disease", "pathology"):
        from bio_toolkit.refdata.disease_taxonomy import guess_pathology_tag
        tag, confident = guess_pathology_tag(label, _disease_rules())
        return {"entity_type": "disease", "entity_id": (tag or None),
                "entity_label": label, "confident": bool(confident and tag)}
    slug = slugify(label)
    return {"entity_type": etype or "concept", "entity_id": (slug or None),
            "entity_label": label, "confident": bool(slug)}
