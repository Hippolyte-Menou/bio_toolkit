"""
Human Protein Atlas (HPA) client.

Ported from the MAC project's `Genes of Interest/scripts/fetch_apis.py`
(`fetch_hpa` + `_extract_hpa_ocular_from_xml`). The original wrote
ocular-keyword-filtered text files for MAC gene assessment; this shared client
strips that filtering and returns the RAW parsed data instead.

Two HPA endpoints are queried (the older /api/tissue and /api/celltype JSON
endpoints were retired -> 404):

  1. search_download.php  -- summary specificity strings (confirms Ensembl id,
     tissue-specificity summary columns). Returns a JSON array of rows.
  2. /{ENSEMBL}.xml        -- the canonical per-gene XML dump (rnaExpression,
     disease links, narrative summaries). Returned as raw bytes; parsed into a
     flat list of {tag, attrib, text} nodes so callers can re-derive whatever
     they need.

Public API:
    fetch_hpa(gene, ensembl_id=None) -> dict
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from typing import Any

import requests

from bio_toolkit.util.retry import retry_on_failure

TIMEOUT = 15

SEARCH_DOWNLOAD_URL = "https://www.proteinatlas.org/api/search_download.php"
# Same projection of specificity columns the original requested.
SEARCH_DOWNLOAD_COLUMNS = (
    "g,gs,eg,up,rnatsm,rnats,rnatsp,rnabcs,rnasm,rnascs,"
    "rnacas,rnarts,rnacts"
)

HEADERS = {
    "User-Agent": "bio-toolkit/0.1 (+research; contact: hippolytefrmenou@gmail.com)",
}


@retry_on_failure(max_retries=2, base_delay=2.0)
def _http_get(url: str, params: dict | None = None, accept: str = "application/json") -> requests.Response:
    headers = dict(HEADERS)
    headers["Accept"] = accept
    r = requests.get(url, params=params, headers=headers, timeout=TIMEOUT)
    r.raise_for_status()
    return r


def fetch_hpa(gene: str, ensembl_id: str | None = None) -> dict[str, Any]:
    """
    Fetch raw Human Protein Atlas data for `gene`.

    If `ensembl_id` is not supplied, it is resolved from the search_download
    response (matching the gene symbol, falling back to the first row), then
    used to fetch the per-gene XML dump.

    Returns a dict:
        {
          "gene": str,
          "ensembl_id": str | None,   # resolved (input or from search_download)
          "search_download": list[dict],            # raw rows (empty on failure)
          "search_download_match": dict | None,     # the row matched for this gene
          "xml_nodes": list[dict],    # flat parse of the per-gene XML, or []
          "errors": dict,             # {"search_download": str, "xml": str} on failure
        }

    Network failures on either endpoint are captured in "errors" rather than
    raised, mirroring the original per-endpoint try/except (so a dead XML
    endpoint still yields the search_download payload).
    """
    out: dict[str, Any] = {
        "gene": gene,
        "ensembl_id": ensembl_id,
        "search_download": [],
        "search_download_match": None,
        "xml_nodes": [],
        "errors": {},
    }

    # Step 1: search_download
    sd_params = {
        "search": gene,
        "format": "json",
        "columns": SEARCH_DOWNLOAD_COLUMNS,
        "compress": "no",
    }
    try:
        r = _http_get(SEARCH_DOWNLOAD_URL, params=sd_params)
        sd = r.json()
        out["search_download"] = sd
        match = None
        for row in sd:
            if row.get("Gene") == gene:
                match = row
                break
        if match is None and sd:
            match = sd[0]
        out["search_download_match"] = match
        if match:
            ens_from_sd = match.get("Ensembl")
            if not out["ensembl_id"] and ens_from_sd:
                out["ensembl_id"] = ens_from_sd
    except Exception as exc:
        out["errors"]["search_download"] = str(exc)

    # Step 2: per-gene XML dump (replaces the retired tissue + celltype JSON
    # endpoints). Only reachable once an Ensembl id is known.
    if out["ensembl_id"]:
        xml_url = f"https://www.proteinatlas.org/{out['ensembl_id']}.xml"
        try:
            r2 = _http_get(xml_url, accept="text/xml")
            out["xml_nodes"] = _parse_hpa_xml(r2.content)
        except Exception as exc:
            out["errors"]["xml"] = str(exc)

    return out


def _parse_hpa_xml(xml_bytes: bytes) -> list[dict[str, Any]]:
    """
    Flatten the HPA per-gene XML into a list of node dicts.

    The original walked this tree flagging ocular keywords; here we return every
    node raw so callers can apply their own filtering. Each node is:
        {"tag": str, "attrib": dict[str, str], "text": str}

    A ParseError yields a single sentinel node {"tag": "_parse_error", ...}.
    """
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError as exc:
        return [{"tag": "_parse_error", "attrib": {}, "text": str(exc)}]

    nodes: list[dict[str, Any]] = []
    for el in root.iter():
        nodes.append({
            "tag": el.tag,
            "attrib": dict(el.attrib),
            "text": (el.text or "").strip(),
        })
    return nodes
