"""
Gene Ontology cellular-component -> French anatomy-note mapping.

Ported from the vault's gene-generation/build_cellular_component_mapping.py.
Pure data (no HTTP): a hand-curated UniProt-cellular-component -> French
anatomy-note table, plus a scan of the anatomy vault folder to report which
target notes actually exist. This module RETURNS parsed structures; the
JSON/review file writing stays in the original script.

The anatomy-root path is an argument (defaulting to the original vault layout)
so scanning the vault is optional — build_mapping() works without it.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# ── Default vault paths (ported from the original layout) ──────────────────────

_VAULT_DIR = Path("C:/Users/hippo/OneDrive/Documents/Genetics/Ophtalmogenetics")
DEFAULT_ANATOMY_ROOT = _VAULT_DIR / "B - Anatomie"
DEFAULT_ANATOMY_MOC = DEFAULT_ANATOMY_ROOT / "Anatomie de l'œil humain - MOC.md"


# ── Manual mapping rules (ported verbatim) ─────────────────────────────────────
#
# High-confidence mappings from lowercase English UniProt cellular-component
# terms to French anatomy note names (path-qualified where the vault nests them).

MANUAL_MAPPINGS: Dict[str, str] = {
    # Photoreceptor structures
    "photoreceptor": "03 - Tunique interne/01 - Photorécepteurs",
    "photoreceptor outer segment": "03 - Tunique interne/01 - Photorécepteurs",
    "photoreceptor inner segment": "03 - Tunique interne/01 - Photorécepteurs",
    "rod outer segment": "03 - Tunique interne/03 - Bâtonnets",
    "cone outer segment": "03 - Tunique interne/04 - Cônes",
    "connecting cilium": "03 - Tunique interne/01 - Photorécepteurs",

    # Cilium and flagellum
    "cilium": "03 - Tunique interne/01 - Photorécepteurs",  # Primary cilium in photoreceptors
    "primary cilium": "03 - Tunique interne/01 - Photorécepteurs",
    "flagellum": "Cytoplasme",  # Generic, no specific note

    # Retinal cell types
    "retina": "02 - Rétine neurosensorielle",
    "retinal pigment epithelium": "03 - Épithélium pigmentaire rétinien",
    "photoreceptor cell": "03 - Tunique interne/01 - Photorécepteurs",
    "rod cell": "03 - Tunique interne/03 - Bâtonnets",
    "cone cell": "03 - Tunique interne/04 - Cônes",
    "retinal ganglion cell": "03 - Tunique interne/07 - Cellules ganglionnaires",
    "bipolar cell": "03 - Tunique interne/10 - Cellules bipolaires",
    "horizontal cell": "03 - Tunique interne/11 - Cellules horizontales",
    "amacrine cell": "03 - Tunique interne/12 - Cellules amacrines",
    "müller cell": "03 - Tunique interne/13 - Cellules gliales de Müller",
    "müller glia": "03 - Tunique interne/13 - Cellules gliales de Müller",

    # Retinal layers
    "outer plexiform layer": "03 - Tunique interne/08 - Couche plexiforme externe",
    "inner plexiform layer": "03 - Tunique interne/09 - Couche plexiforme interne",

    # Anterior segment
    "cornea": "Cornée",
    "corneal epithelium": "Cornée",
    "corneal stroma": "Cornée",
    "corneal endothelium": "Cornée",
    "iris": "02 - Iris",
    "lens": "03 - Cristallin",
    "crystalline lens": "03 - Cristallin",
    "lens capsule": "03 - Cristallin",
    "lens epithelium": "03 - Cristallin",
    "lens fiber": "03 - Cristallin",
    "ciliary body": "03 - Corps ciliaire",

    # Uvea and choroid
    "choroid": "04 - Choroïde",
    "uvea": "02 - Tunique moyenne/Uvée",

    # Vitreous
    "vitreous": "04 - Corps vitré",
    "vitreous body": "04 - Corps vitré",
    "vitreous humor": "04 - Corps vitré",

    # Optic nerve
    "optic nerve": "07 - Nerf optique",
    "optic disc": "07 - Nerf optique",
    "optic cup": "07 - Nerf optique",

    # Generic subcellular (no specific anatomy mapping)
    "nucleus": "Noyau cellulaire",
    "cytoplasm": "Cytoplasme",
    "cytosol": "Cytoplasme",
    "mitochondrion": "Mitochondrie",
    "mitochondria": "Mitochondrie",
    "endoplasmic reticulum": "Réticulum endoplasmique",
    "golgi apparatus": "Appareil de Golgi",
    "golgi": "Appareil de Golgi",
    "lysosome": "Lysosome",
    "peroxisome": "Peroxysome",
    "ribosome": "Ribosome",
    "cell membrane": "Membrane plasmique",
    "plasma membrane": "Membrane plasmique",
    "cell projection": "Projection cellulaire",
    "cytoskeleton": "Cytosquelette",
    "microtubule": "Microtubule",
    "actin": "Filament d'actine",
    "synapse": "Synapse",
    "synaptic vesicle": "Vésicule synaptique",
}


def load_anatomy_notes(
    anatomy_root: Path = DEFAULT_ANATOMY_ROOT,
    anatomy_moc_path: Optional[Path] = None,
) -> List[str]:
    """
    Collect anatomy note names from the vault folder + the anatomy MOC wikilinks.

    Scans *.md under `anatomy_root` (skipping MOCs, Books, and 'Anatomie de'
    files), then adds any wikilink targets referenced in the MOC. Returns a
    de-duplicated list. If `anatomy_root` does not exist, returns [].
    """
    anatomy_root = Path(anatomy_root)
    if anatomy_moc_path is None:
        anatomy_moc_path = anatomy_root / "Anatomie de l'œil humain - MOC.md"

    notes: List[str] = []
    if anatomy_root.exists():
        for md_file in anatomy_root.rglob("*.md"):
            note_name = md_file.stem
            if not any(suffix in note_name for suffix in [" - MOC", " - Book", "Anatomie de"]):
                notes.append(note_name)

    anatomy_moc_path = Path(anatomy_moc_path)
    if anatomy_moc_path.exists():
        content = anatomy_moc_path.read_text(encoding="utf-8")
        wikilink_pattern = re.compile(r"\[\[([^\]]+)\]\]")
        for match in wikilink_pattern.findall(content):
            if match not in notes and not match.endswith(" - MOC"):
                notes.append(match)

    return list(set(notes))


def categorize_component(component: str) -> str:
    """
    Bucket a (lowercase) cellular-component term into a review category.

    Mirrors the original review-file grouping logic.
    """
    if any(term in component for term in
           ["photoreceptor", "rod", "cone", "cilium", "outer segment", "inner segment"]):
        return "Photoreceptor structures"
    if any(term in component for term in
           ["retina", "ganglion", "bipolar", "horizontal", "amacrine", "müller"]):
        return "Retinal cells"
    if "plexiform" in component:
        return "Retinal layers"
    if any(term in component for term in ["cornea", "iris", "lens", "ciliary"]):
        return "Anterior segment"
    if any(term in component for term in ["choroid", "uvea"]):
        return "Uvea and choroid"
    if any(term in component for term in ["vitreous", "optic"]):
        return "Other ocular"
    return "Generic subcellular"


def build_mapping(
    anatomy_root: Path = DEFAULT_ANATOMY_ROOT,
    anatomy_moc_path: Optional[Path] = None,
    scan_anatomy: bool = True,
) -> Dict:
    """
    Build the cellular-component mapping structure.

    Returns a dict mirroring the original JSON output:
        {
          "metadata": {"anatomy_notes": int, "confident_mappings": int},
          "mappings": {component_term: french_note_name, ...},
        }

    `mappings` is the (copied) curated MANUAL_MAPPINGS — the original only emits
    the manual mappings (fuzzy matching was a noted future enhancement). When
    `scan_anatomy` is True the anatomy vault is scanned to populate
    metadata.anatomy_notes; pass False (or a missing root) to skip the scan.
    """
    if scan_anatomy:
        anatomy_notes = load_anatomy_notes(anatomy_root, anatomy_moc_path)
    else:
        anatomy_notes = []

    confident_mappings = dict(MANUAL_MAPPINGS)

    return {
        "metadata": {
            "anatomy_notes": len(anatomy_notes),
            "confident_mappings": len(confident_mappings),
        },
        "mappings": confident_mappings,
    }


def grouped_for_review(
    mappings: Optional[Dict[str, str]] = None,
) -> Dict[str, List[Tuple[str, str]]]:
    """
    Group the mappings into the review categories used by the original review MD.

    Returns {category: [(component, anatomy_note), ...]} with components sorted.
    Defaults to the full curated MANUAL_MAPPINGS.
    """
    if mappings is None:
        mappings = MANUAL_MAPPINGS

    categories: Dict[str, List[Tuple[str, str]]] = {
        "Photoreceptor structures": [],
        "Retinal cells": [],
        "Retinal layers": [],
        "Anterior segment": [],
        "Uvea and choroid": [],
        "Other ocular": [],
        "Generic subcellular": [],
    }
    for component, anatomy in sorted(mappings.items()):
        categories[categorize_component(component)].append((component, anatomy))
    return categories
