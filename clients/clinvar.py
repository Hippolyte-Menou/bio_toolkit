"""
ClinVar (NCBI E-utilities) query client.

Ported from the MAC project's Genes of Interest `fetch_apis.py` (`fetch_clinvar`).
This is the RAW data fetcher only: it runs an esearch on the clinvar db followed
by an esummary for the returned UIDs, and returns the parsed JSON for both.

What was stripped vs. the MAC source:
  - The hardcoded ocular-phenotype clause in the esearch `term`
    (`... AND (eye[disease/phenotype] OR retinal[...] OR coloboma[...] OR
    microphthalmia[...] OR anophthalmia[...])`). That is the MAC domain filter
    and stays in MAC. Here the default query is the bare `{gene}[gene]`; callers
    that need a phenotype filter pass their own `term`.
  - The ocular-keyword flagging of titles/traits, the "RELEVANT FINDINGS"
    section, and the on-disk output files.

NCBI courtesy pacing (NCBI_SLEEP) between the esearch and esummary calls is
preserved.
"""

from __future__ import annotations

import time
from typing import Any

import requests

from bio_toolkit.util.retry import retry_on_failure

TIMEOUT = 15
NCBI_SLEEP = 0.35

ESEARCH_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
ESUMMARY_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi"

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


def esearch(gene: str, term: str | None = None, retmax: int = 10) -> dict[str, Any]:
    """
    Run a ClinVar esearch and return the parsed JSON.

    By default the search term is the bare `{gene}[gene]` (no phenotype filter).
    Pass `term` to override completely (it is sent verbatim as the NCBI `term`).
    """
    if term is None:
        term = f"{gene}[gene]"
    params = {"db": "clinvar", "term": term, "retmode": "json", "retmax": retmax}
    r = _http_get(ESEARCH_URL, params=params)
    return r.json()


def esummary(ids: list[str]) -> dict[str, Any]:
    """Run a ClinVar esummary for a list of UIDs and return the parsed JSON."""
    params = {"db": "clinvar", "id": ",".join(ids), "retmode": "json"}
    r = _http_get(ESUMMARY_URL, params=params)
    return r.json()


def fetch_clinvar(
    gene: str,
    term: str | None = None,
    retmax: int = 10,
) -> dict[str, Any]:
    """
    Fetch ClinVar records for a gene: esearch, then esummary of the returned UIDs.

    Returns a combined raw dict:
        {"esearch": <esearch json>, "esummary": <esummary json or None>}

    `esummary` is None when esearch returned no UIDs. NCBI courtesy pacing is
    applied between the two calls. Raises on HTTP error (the MAC source swallowed
    these and wrote an error file; that behaviour is left to the caller here).
    """
    search = esearch(gene, term=term, retmax=retmax)
    combined: dict[str, Any] = {"esearch": search, "esummary": None}

    es = search.get("esearchresult") or {}
    ids = es.get("idlist") or []
    if not ids:
        return combined

    time.sleep(NCBI_SLEEP)
    combined["esummary"] = esummary(ids)
    return combined
