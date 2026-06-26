"""
PanelApp England client — unified, reconciled from three vault/MAC sources.

This is the single PanelApp fetcher for bio_toolkit, replacing three previously
duplicated implementations:

  * MAC project  — `Literature data/PanelApp/panelapp_fetcher.py`
        Downloaded the official panel TSV, filtered to Green + Amber via the
        `GEL_Status` column ({2, 3}), and returned only the sorted gene symbols.
        Parsed by named columns with `csv.DictReader` (robust to column order).

  * Vault        — `Ophtalmogenetics/_assets/code/gene-generation/panelapp_fetcher.py`
        Downloaded the same TSV, mapped the col-3 confidence string to
        green/amber/red and the col-7 inheritance string to short clinical
        notation (AR / AD / XL / MT / ...), keyed records by gene symbol, and
        carried the panel slug.

  * Bot          — `Ophtalmogenetics/_assets/code/Zotero-automation/build_genes_yml.py`
        Consumed PanelApp data indirectly (via vault frontmatter) to group genes
        into per-panel/per-symbol collections. Its requirement on a PanelApp
        client is a flat, per-gene list keyed by `symbol` with panel membership,
        so genes can be grouped without re-reading vault notes.

The SUPERSET return record therefore carries everything any consumer extracted:
gene `symbol`, `confidence`, `inheritance`, `panel_id`, `panel_name`, and the
raw `gel_status` code.

This module RETURNS parsed data only (a list of dicts). It does NOT write
`panelapp_data.json`, render DataviewJS vault notes, or emit `genes.yml` — those
remain concerns of the original MAC / vault / bot callers.
"""

from __future__ import annotations

import csv
import io
from typing import Any, Optional

import requests

from bio_toolkit.util.retry import retry_on_failure

TIMEOUT = 30

# Official PanelApp England TSV download endpoint. The trailing token is a
# version placeholder PanelApp accepts for "latest" (matches the MAC source's
# `.../download/01234/`). Format with the integer panel id.
PANELAPP_DOWNLOAD_URL = (
    "https://panelapp.genomicsengland.co.uk/panels/{id}/download/01234/"
)

_HEADERS = {
    "Accept": "text/tab-separated-values, text/plain, */*",
    "User-Agent": "bio-toolkit/0.1 (+research)",
}

# GEL_Status codes (PanelApp confidence): 0 = not evaluated, 1 = Red,
# 2 = Amber, 3 = Green. Map the raw code to a clean confidence label.
GEL_STATUS_TO_CONFIDENCE = {
    "3": "green",
    "2": "amber",
    "1": "red",
    "0": "grey",
}

# Green + Amber — the actionable subset the MAC source kept.
GREEN_AMBER_STATUS = {"2", "3"}

# Highest-confidence-wins priority (vault source's CONFIDENCE_PRIORITY).
CONFIDENCE_PRIORITY = ["green", "amber", "red", "grey"]

# Verbose PanelApp inheritance string -> short clinical notation.
# Carried verbatim from the vault source's PanelAppConfig.INHERITANCE_MAP.
INHERITANCE_MAP = {
    "BIALLELIC, autosomal or pseudoautosomal":
        "AR",
    "MONOALLELIC, autosomal or pseudoautosomal, NOT imprinted":
        "AD",
    "MONOALLELIC, autosomal or pseudoautosomal, imprinted status unknown":
        "AD",
    "BOTH monoallelic and biallelic, autosomal or pseudoautosomal":
        "AD/AR",
    "BOTH monoallelic and biallelic (but BIALLELIC mutations cause a more "
    "SEVERE disease form), autosomal or pseudoautosomal":
        "AD/AR (AR more severe)",
    "X-LINKED: hemizygous mutation in males, biallelic mutations in females":
        "XL",
    "X-LINKED: hemizygous mutation in males, monoallelic mutations in females "
    "may cause disease (may be less severe, later onset than males)":
        "XL (carrier females affected)",
    "MITOCHONDRIAL":
        "MT",
    "Other":
        "other",
}


def _clean_inheritance(raw: Optional[str]) -> Optional[str]:
    """Map a verbose PanelApp inheritance string to short notation, or None."""
    raw = (raw or "").strip()
    if not raw:
        return None
    return INHERITANCE_MAP.get(raw, raw)


@retry_on_failure(max_retries=2, base_delay=2.0)
def _get(url: str) -> requests.Response:
    """GET with retry/backoff on transient failures. Raises on HTTP error."""
    r = requests.get(url, headers=_HEADERS, timeout=TIMEOUT)
    r.raise_for_status()
    return r


def download_panel_tsv(panel_id: int) -> str:
    """Download one PanelApp panel and return the raw TSV text."""
    r = _get(PANELAPP_DOWNLOAD_URL.format(id=panel_id))
    return r.text


def parse_panel_tsv(
    tsv_text: str,
    panel_id: int,
    panel_name: Optional[str] = None,
    green_amber_only: bool = True,
) -> list[dict[str, Any]]:
    """
    Parse one PanelApp TSV into the unified per-gene record list.

    Uses named columns via ``csv.DictReader`` (robust to column reordering —
    the MAC source's approach) rather than positional indices. Each row that
    is an ``Entity type == "gene"`` with a non-empty symbol becomes one dict:

        {
            "symbol":      str,            # gene symbol (verbatim casing)
            "confidence":  str,            # "green" / "amber" / "red" / "grey"
            "inheritance": str | None,     # short notation, e.g. "AR", "XL"
            "panel_id":    int,
            "panel_name":  str | None,
            "gel_status":  str,            # raw GEL_Status code, e.g. "3"
        }

    Parameters
    ----------
    tsv_text : str
        Raw PanelApp TSV content.
    panel_id : int
        Panel id, stamped onto every record.
    panel_name : str, optional
        Human-readable panel name, stamped onto every record.
    green_amber_only : bool
        If True (default — the MAC source's behaviour), keep only
        GEL_Status in {2, 3} (Amber + Green). If False, return every
        gene row regardless of confidence.
    """
    reader = csv.DictReader(io.StringIO(tsv_text), delimiter="\t")
    records: list[dict[str, Any]] = []

    for row in reader:
        if (row.get("Entity type") or "").strip() != "gene":
            continue

        symbol = (row.get("Gene Symbol") or "").strip()
        if not symbol:
            continue

        gel_status = (row.get("GEL_Status") or "").strip()
        if green_amber_only and gel_status not in GREEN_AMBER_STATUS:
            continue

        confidence = GEL_STATUS_TO_CONFIDENCE.get(gel_status, "grey")
        inheritance = _clean_inheritance(row.get("Model_Of_Inheritance"))

        records.append({
            "symbol": symbol,
            "confidence": confidence,
            "inheritance": inheritance,
            "panel_id": panel_id,
            "panel_name": panel_name,
            "gel_status": gel_status,
        })

    return records


def fetch_panel(
    panel_id: int,
    panel_name: Optional[str] = None,
    green_amber_only: bool = True,
) -> list[dict[str, Any]]:
    """
    Download and parse one PanelApp panel into the unified record list.

    Returns a list of per-gene dicts (see :func:`parse_panel_tsv` for the
    schema). Raises ``requests.HTTPError`` / ``requests.RequestException`` on a
    download failure (the MAC/vault sources logged-and-skipped; that policy is
    left to the caller here).
    """
    tsv_text = download_panel_tsv(panel_id)
    return parse_panel_tsv(
        tsv_text,
        panel_id=panel_id,
        panel_name=panel_name,
        green_amber_only=green_amber_only,
    )


def fetch_panels(
    panels: dict[int, str],
    green_amber_only: bool = True,
) -> list[dict[str, Any]]:
    """
    Download and parse multiple panels, returning one flat record list.

    Parameters
    ----------
    panels : dict[int, str]
        Mapping of ``panel_id -> panel_name``.
    green_amber_only : bool
        Forwarded to :func:`fetch_panel`.

    The flat list (genes from all panels concatenated, one record per
    gene-per-panel) is what the bot consumer needs to group by panel or by
    symbol without re-reading vault frontmatter.
    """
    records: list[dict[str, Any]] = []
    for panel_id, panel_name in panels.items():
        records.extend(
            fetch_panel(panel_id, panel_name=panel_name,
                        green_amber_only=green_amber_only)
        )
    return records


def highest_confidence(confidences: list[str]) -> str:
    """
    Return the highest-priority confidence among the given labels.

    Reconciles the vault source's ``derive_highest_confidence`` — used when one
    gene appears across several panels and a single canonical confidence is
    wanted. Defaults to ``"grey"`` if none match.
    """
    seen = set(confidences)
    for level in CONFIDENCE_PRIORITY:
        if level in seen:
            return level
    return "grey"
