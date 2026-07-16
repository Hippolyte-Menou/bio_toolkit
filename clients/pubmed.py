"""
PubMed (NCBI E-utilities) query client with abstracts.

The `db=pubmed` counterpart to clinvar.py: esearch -> esummary (title/journal/
year) -> efetch (abstract + MeSH + publication types, parsed from XML). No such
client existed in bio_toolkit; assess-genes' inline `fetch_pubmed` in the MAC
scripts is retired in favour of this canonical one.

NCBI credentials come from bio_toolkit.config and are OPTIONAL: a key lifts the
rate limit to 10 req/s, its absence falls back to the anonymous 3 req/s tier.
The email is attached for NCBI etiquette. Courtesy pacing (NCBI_SLEEP) is applied
between the three calls, matching clinvar.py.
"""

from __future__ import annotations

import time
import xml.etree.ElementTree as ET
from typing import Any

import requests

from bio_toolkit import config
from bio_toolkit.util.retry import retry_on_failure

TIMEOUT = 20
NCBI_SLEEP = 0.35

ESEARCH_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
ESUMMARY_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi"
EFETCH_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"

_HEADERS = {"User-Agent": "bio-toolkit/0.1 (+research)"}


def _base_params() -> dict[str, str]:
    """Common query params: optional api_key + email + tool (etiquette)."""
    params: dict[str, str] = {"tool": "bio-toolkit", "email": config.ncbi_email()}
    key = config.ncbi_api_key()
    if key:
        params["api_key"] = key
    return params


@retry_on_failure(max_retries=2, base_delay=2.0)
def _http_get(url: str, params: dict) -> requests.Response:
    r = requests.get(url, params=params, headers=_HEADERS, timeout=TIMEOUT)
    r.raise_for_status()
    return r


def esearch(term: str, retmax: int = 40, sort: str = "relevance") -> dict[str, Any]:
    """Run a PubMed esearch. Returns {"count", "idlist", "raw"}."""
    params = {**_base_params(), "db": "pubmed", "term": term,
              "retmode": "json", "retmax": str(retmax), "sort": sort}
    data = _http_get(ESEARCH_URL, params).json()
    es = data.get("esearchresult") or {}
    return {"count": es.get("count", "0"), "idlist": es.get("idlist") or [], "raw": data}


def esummary(pmids: list[str]) -> dict[str, Any]:
    """esummary for a PMID list (title/journal/year). Empty dict if no ids."""
    if not pmids:
        return {}
    params = {**_base_params(), "db": "pubmed", "id": ",".join(pmids), "retmode": "json"}
    return _http_get(ESUMMARY_URL, params).json()


def efetch(pmids: list[str]) -> dict[str, dict]:
    """efetch abstracts + MeSH + publication types, parsed from PubMed XML.

    Returns {pmid: {"abstract": str, "mesh": [str], "pubtypes": [str]}}.
    Articles without an abstract get an empty string. Empty input -> {}.
    """
    if not pmids:
        return {}
    params = {**_base_params(), "db": "pubmed", "id": ",".join(pmids),
              "retmode": "xml", "rettype": "abstract"}
    xml_text = _http_get(EFETCH_URL, params).text
    out: dict[str, dict] = {}
    root = ET.fromstring(xml_text)
    for art in root.findall(".//PubmedArticle"):
        pmid_el = art.find(".//MedlineCitation/PMID")
        if pmid_el is None or not (pmid_el.text or "").strip():
            continue
        pmid = pmid_el.text.strip()

        # Abstract: join AbstractText sections, prefixing structured labels.
        chunks: list[str] = []
        for at in art.findall(".//Abstract/AbstractText"):
            text = "".join(at.itertext()).strip()
            if not text:
                continue
            label = at.get("Label")
            chunks.append(f"{label}: {text}" if label else text)
        abstract = " ".join(chunks)

        mesh = [d.text.strip() for d in art.findall(".//MeshHeading/DescriptorName")
                if d.text and d.text.strip()]
        pubtypes = [p.text.strip() for p in art.findall(".//PublicationType")
                    if p.text and p.text.strip()]
        out[pmid] = {"abstract": abstract, "mesh": mesh, "pubtypes": pubtypes}
    return out


def fetch_pubmed(term: str, retmax: int = 40, sort: str = "relevance") -> dict[str, Any]:
    """Full tier fetch: esearch -> esummary -> efetch, with NCBI pacing.

    Returns {"esearch": <dict>, "esummary": <json|None>, "efetch": {pmid: {...}}}.
    esummary is None and efetch is {} when esearch returned no PMIDs.
    """
    search = esearch(term, retmax=retmax, sort=sort)
    ids = search["idlist"]
    combined: dict[str, Any] = {"esearch": search, "esummary": None, "efetch": {}}
    if not ids:
        return combined
    time.sleep(NCBI_SLEEP)
    combined["esummary"] = esummary(ids)
    time.sleep(NCBI_SLEEP)
    combined["efetch"] = efetch(ids)
    return combined
