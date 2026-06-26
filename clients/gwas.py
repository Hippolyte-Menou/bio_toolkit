"""
GWAS Catalog (EBI REST) query client.

Ported from the MAC project's Genes of Interest `fetch_apis.py` (`fetch_gwas`).
This is the RAW data fetcher only. It reproduces the three-step HAL traversal the
MAC source uses (the old `/genes/{name}/associations` endpoint was retired):

  1. /singleNucleotidePolymorphisms/search/findByGene?geneName=GENE
     -> SNPs mapped to the gene
  2. for the top-N SNPs, follow the `associations` HAL link
     -> associations, each carrying an `efoTraits` HAL link
  3. dedupe efoTrait URLs and fetch each (capped) -> trait labels/uris

What was stripped vs. the MAC source:
  - The ocular-keyword flagging of trait labels, the "RELEVANT FINDINGS"
    section, and the on-disk output files. The function returns the raw
    collected payloads; downstream MAC filtering stays in MAC.

The MAC source's compact `findByGene_summary` projection (it deliberately avoids
storing the 1-2 MB HAL payload) is preserved as the `findByGene_summary` key.
"""

from __future__ import annotations

from typing import Any

import requests

from bio_toolkit.util.retry import retry_on_failure

TIMEOUT = 15

FIND_BY_GENE_URL = (
    "https://www.ebi.ac.uk/gwas/rest/api/singleNucleotidePolymorphisms/"
    "search/findByGene"
)

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


def find_by_gene(gene: str, size: int = 200) -> dict[str, Any]:
    """Run findByGene and return the parsed HAL JSON."""
    return _http_get(FIND_BY_GENE_URL, params={"geneName": gene, "size": size}).json()


def fetch_gwas(
    gene: str,
    max_snps: int = 25,
    max_traits: int = 30,
    size: int = 200,
) -> dict[str, Any]:
    """
    Fetch GWAS Catalog associations for a gene via the HAL traversal.

    Returns a raw dict:
        {
          "findByGene_summary": {"total_snps": int,
                                 "snps": [{"rsId", "functionalClass"}, ...]},
          "associations_per_snp": {rsId: {"n": int} | {"error": str}},
          "traits": {efoTrait_url: [<efoTrait dict>, ...] | {"error": str}},
        }

    The `associations_per_snp` / `traits` per-item error captures mirror the
    MAC source's best-effort traversal: a failed sub-request is recorded and the
    walk continues rather than aborting. The top-level findByGene call still
    raises on HTTP error (the MAC source wrote an error file there; left to the
    caller here).
    """
    snp_data = find_by_gene(gene, size=size)

    snps = (snp_data.get("_embedded") or {}).get("singleNucleotidePolymorphisms", [])
    raw: dict[str, Any] = {
        "findByGene_summary": {
            "total_snps": len(snps),
            "snps": [
                {"rsId": s.get("rsId"), "functionalClass": s.get("functionalClass")}
                for s in snps
            ],
        },
        "associations_per_snp": {},
        "traits": {},
    }
    if not snps:
        return raw

    # GWAS Catalog returns SNPs already ranked; take the top N.
    snps = snps[:max_snps]

    trait_urls: dict[str, list[str]] = {}  # trait_url -> [rsIds]
    for s in snps:
        rs = s.get("rsId")
        href = (s.get("_links") or {}).get("associations", {}).get("href")
        if not (rs and href):
            continue
        try:
            ar = _http_get(href).json()
        except requests.RequestException as exc:
            raw["associations_per_snp"][rs] = {"error": str(exc)}
            continue
        assocs = (ar.get("_embedded") or {}).get("associations", [])
        raw["associations_per_snp"][rs] = {"n": len(assocs)}
        for a in assocs:
            efo = (a.get("_links") or {}).get("efoTraits", {}).get("href")
            if efo:
                trait_urls.setdefault(efo, []).append(rs)

    for turl, _rs_list in list(trait_urls.items())[:max_traits]:
        try:
            tr = _http_get(turl).json()
        except requests.RequestException as exc:
            raw["traits"][turl] = {"error": str(exc)}
            continue
        traits = (tr.get("_embedded") or {}).get("efoTraits", [])
        raw["traits"][turl] = traits

    return raw
