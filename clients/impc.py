"""
IMPC (International Mouse Phenotyping Consortium) client.

Ported from the MAC project's `Genes of Interest/scripts/fetch_apis.py`
(`fetch_impc`). The original filtered genotype-phenotype rows down to ocular MP
terms and wrote a text file for MAC gene assessment; this shared client strips
that filtering and returns the RAW genotype-phenotype documents.

Endpoint:
    https://www.ebi.ac.uk/mi/impc/solr/genotype-phenotype/select
    (Solr core, queried by marker_symbol)

Public API:
    fetch_impc(gene, rows=200) -> dict
"""

from __future__ import annotations

from typing import Any

import requests

from bio_toolkit.util.retry import retry_on_failure

TIMEOUT = 15

IMPC_SOLR_URL = "https://www.ebi.ac.uk/mi/impc/solr/genotype-phenotype/select"

# Field list ported verbatim from the original fetch_impc().
IMPC_FIELDS = (
    "marker_symbol,allele_symbol,top_level_mp_term_name,"
    "mp_term_name,mp_term_id,p_value,effect_size,phenotyping_center"
)

HEADERS = {
    "Accept": "application/json",
    "User-Agent": "bio-toolkit/0.1 (+research; contact: hippolytefrmenou@gmail.com)",
}


@retry_on_failure(max_retries=2, base_delay=2.0)
def _http_get(url: str, params: dict) -> requests.Response:
    r = requests.get(url, params=params, headers=HEADERS, timeout=TIMEOUT)
    r.raise_for_status()
    return r


def fetch_impc(gene: str, rows: int = 200) -> dict[str, Any]:
    """
    Fetch raw IMPC genotype-phenotype records for `gene`.

    Returns a dict:
        {
          "gene": str,
          "num_found": int,            # Solr response.numFound
          "docs": list[dict],          # raw genotype-phenotype documents
          "raw": dict,                 # full Solr JSON response
        }

    `docs` is the unfiltered list of phenotype records (the caller decides what
    is ocular-relevant). No ocular keyword filtering is applied.
    """
    params = {
        "q": f"marker_symbol:{gene}",
        "rows": rows,
        "wt": "json",
        "fl": IMPC_FIELDS,
    }
    r = _http_get(IMPC_SOLR_URL, params=params)
    data = r.json()

    response = data.get("response") or {}
    docs = response.get("docs", [])
    return {
        "gene": gene,
        "num_found": response.get("numFound", len(docs)),
        "docs": docs,
        "raw": data,
    }
