"""
mygene.info query client.

Ported from the MAC project's Genes of Interest `fetch_apis.py` (`fetch_mygene`).
This is the RAW data fetcher only: it issues the mygene.info v3 query and returns
the parsed JSON. The ocular/MAC-keyword filtering (GO-term flagging, "RELEVANT
FINDINGS" sections, on-disk output files) that wraps this call in the MAC project
is intentionally NOT carried over — that domain filter stays in MAC.
"""

from __future__ import annotations

from typing import Any

import requests

from bio_toolkit.util.retry import retry_on_failure

TIMEOUT = 15

MYGENE_QUERY_URL = "https://mygene.info/v3/query"

# Fields requested from mygene.info (verbatim from the MAC source).
DEFAULT_FIELDS = "symbol,alias,name,summary,go,pathway,MIM,Ensembl,refseq,uniprot"

_HEADERS = {
    "Accept": "application/json",
    "User-Agent": "bio-toolkit/0.1 (+research)",
}


@retry_on_failure(max_retries=2, base_delay=2.0)
def _http_get(url: str, params: dict | None = None) -> requests.Response:
    """GET with retry/backoff on transient failures. Raises on HTTP error."""
    r = requests.get(url, params=params, headers=_HEADERS, timeout=TIMEOUT)
    r.raise_for_status()
    return r


def fetch_mygene(gene: str, fields: str = DEFAULT_FIELDS) -> dict[str, Any]:
    """
    Query mygene.info for a human gene symbol and return the parsed JSON dict.

    The returned dict is the raw mygene.info response, e.g.:
        {"hits": [{"symbol": ..., "name": ..., "go": {...}, ...}], "total": N, ...}

    Raises requests.HTTPError / requests.RequestException on failure (the MAC
    source swallowed these and wrote an error file; that error-handling +
    file-writing behaviour is a MAC concern and is left to the caller here).
    """
    params = {
        "q": f"symbol:{gene}",
        "species": "human",
        "fields": fields,
    }
    r = _http_get(MYGENE_QUERY_URL, params=params)
    return r.json()


def parse_gene_info(data: dict[str, Any]) -> dict[str, Any]:
    """
    Extract the convenience projection the MAC pipeline returned for downstream
    calls: the canonical Ensembl gene id (ENSG...) and the alias list.

    Mirrors the return value of `fetch_mygene` in the original source, minus all
    ocular-keyword flagging. Returns {"ensembl": str | None, "aliases": [str]}.
    """
    hits = data.get("hits") or []
    if not hits:
        return {"ensembl": None, "aliases": []}

    h = hits[0]

    ens = h.get("Ensembl") or {}
    if isinstance(ens, list):
        ens_ids = [e.get("gene") for e in ens if isinstance(e, dict)]
    elif isinstance(ens, dict):
        ens_ids = [ens.get("gene")]
    else:
        ens_ids = []

    aliases = h.get("alias")
    if isinstance(aliases, str):
        aliases = [aliases]

    return {
        "ensembl": next((e for e in ens_ids if e and e.startswith("ENSG")), None),
        "aliases": aliases or [],
    }
