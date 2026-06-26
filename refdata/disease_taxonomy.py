"""
Disease taxonomy builder — UniProt disease annotations -> acronym-keyed lookup.

Ported from the vault's disease-taxonomy/build_disease_lookup.py. This module
keeps the data/domain logic only and RETURNS parsed structures; all on-disk
output (disease_lookup.json, pathology_tag_mapping.json, the review markdown,
and the API cache file) stays in the original script.

Three layers, smallest-dependency first:

  1. Pure transforms — strip_trailing_number, guess_pathology_tag,
     extract_diseases_from_uniprot_json, build_disease_lookup. No I/O.
  2. File readers (parameterized paths) — load_pathology_rules,
     extract_panelapp_genes, load_hgnc_index.
  3. HTTP fetchers — fetch_uniprot_diseases, fetch_batch_diseases,
     query_all_genes. Use bio_toolkit.util.retry.retry_on_failure.

Input paths are arguments (defaulting to the original vault layout) so the
module does not hard-depend on the vault being present.
"""

from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests
import yaml

from bio_toolkit.util.retry import retry_on_failure

# ── Default vault paths (ported from the original SCRIPT_DIR-relative layout) ──

_THIS = Path(__file__).resolve()
# Original layout: <vault>/_assets/code/disease-taxonomy/build_disease_lookup.py
# with data under <vault>/_assets/code/gene-generation/data.
_VAULT_CODE_DIR = Path(
    "C:/Users/hippo/OneDrive/Documents/Genetics/Ophtalmogenetics/_assets/code"
)
_GENE_GEN_DIR = _VAULT_CODE_DIR / "gene-generation"
_DATA_DIR = _GENE_GEN_DIR / "data"
_VAULT_DIR = _VAULT_CODE_DIR.parent.parent

DEFAULT_TAXONOMY_YAML = _GENE_GEN_DIR / "data" / "taxonomy.yaml"
DEFAULT_PANELAPP_TABLE = _VAULT_DIR / "G - Genes" / "PanelApp England" / "01 - Gene Table.md"
DEFAULT_HGNC_TSV = _DATA_DIR / "hgnc_complete_set.txt"

# ── Constants (ported) ────────────────────────────────────────────────────────

UNIPROT_API_URL = "https://rest.uniprot.org/uniprotkb/{}.json"
SEARCH_URL = "https://rest.uniprot.org/uniprotkb/search"
REQUEST_TIMEOUT = 15
BATCH_SIZE = 50  # UniProt accessions per search API call


# ═══════════════════════════════════════════════════════════════════════════
# File readers (parameterized paths)
# ═══════════════════════════════════════════════════════════════════════════

def load_pathology_rules(
    taxonomy_path: Path = DEFAULT_TAXONOMY_YAML,
) -> List[Tuple[re.Pattern, str]]:
    """
    Load and compile pathology-tag rules from taxonomy.yaml.

    Order matters: first match wins (more specific patterns go first), so the
    list order from the YAML is preserved.
    """
    with open(taxonomy_path, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    rules = data.get("pathology_tag_rules", []) if data else []
    return [
        (re.compile(r["pattern"], re.IGNORECASE), r["tag"])
        for r in rules
    ]


def extract_panelapp_genes(panelapp_path: Path = DEFAULT_PANELAPP_TABLE) -> List[str]:
    """
    Parse a PanelApp gene table and extract gene symbols from wikilinks.

    Matches [[G - Genes/SYMBOL|SYMBOL]] (the pipe may be escaped as \\| inside
    markdown tables). Deduplicates case-insensitively while preserving order.
    """
    panelapp_path = Path(panelapp_path)
    if not panelapp_path.exists():
        raise FileNotFoundError(f"PanelApp table not found: {panelapp_path}")

    wikilink_re = re.compile(r"\[\[G - Genes/([A-Za-z0-9_-]+)\\?\|")

    genes: List[str] = []
    with open(panelapp_path, "r", encoding="utf-8") as f:
        for line in f:
            match = wikilink_re.search(line)
            if match:
                genes.append(match.group(1))

    seen = set()
    unique_genes: List[str] = []
    for g in genes:
        g_upper = g.upper()
        if g_upper not in seen:
            seen.add(g_upper)
            unique_genes.append(g)
    return unique_genes


def load_hgnc_index(hgnc_path: Path = DEFAULT_HGNC_TSV) -> Dict[str, Dict]:
    """
    Load an HGNC complete-set TSV and build an upper(symbol) -> row-dict index.

    Uses the stdlib csv reader (the original used pandas; the parsed result —
    a per-symbol dict of column values — is identical for the columns this
    module consumes: `symbol` and `uniprot_ids`).
    """
    import csv

    hgnc_path = Path(hgnc_path)
    index: Dict[str, Dict] = {}
    with open(hgnc_path, "r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f, delimiter="\t")
        for row in reader:
            symbol = str(row.get("symbol", "") or "").upper()
            if symbol and symbol != "NAN":
                index[symbol] = dict(row)
    return index


# ═══════════════════════════════════════════════════════════════════════════
# Pure transforms
# ═══════════════════════════════════════════════════════════════════════════

def get_uniprot_id(hgnc_entry: Dict) -> Optional[str]:
    """Extract the primary (first) UniProt accession from an HGNC row dict."""
    uid_raw = str(hgnc_entry.get("uniprot_ids", ""))
    if not uid_raw or uid_raw == "nan":
        return None
    ids = [u.strip() for u in uid_raw.split("|") if u.strip()]
    return ids[0] if ids else None


def extract_diseases_from_uniprot_json(data: Dict) -> List[Dict]:
    """Extract disease entries from a single UniProt entry JSON."""
    diseases: List[Dict] = []
    comments = data.get("comments", [])

    for comment in comments:
        if comment.get("commentType") != "DISEASE":
            continue
        disease = comment.get("disease", {})
        if not disease:
            continue

        disease_name = disease.get("diseaseId", "")
        acronym = disease.get("acronym", "")
        description = disease.get("description", "")

        omim_id = ""
        crossref = disease.get("diseaseCrossReference", {})
        if crossref.get("database") == "MIM":
            omim_id = crossref.get("id", "")

        pubmed_ids: List[str] = []
        for evidence in comment.get("evidences", []):
            if evidence.get("source") == "PubMed":
                pid = evidence.get("id", "")
                if pid:
                    pubmed_ids.append(pid)

        diseases.append({
            "disease_name": disease_name,
            "acronym": acronym,
            "description": description,
            "omim_id": omim_id,
            "pubmed_ids": pubmed_ids,
        })

    return diseases


def strip_trailing_number(name: str) -> str:
    """
    Remove a trailing number from a disease name for grouping.

    'Retinitis pigmentosa 3' -> 'Retinitis pigmentosa'
    'age-related, 2' -> 'age-related'   (trailing comma also stripped)
    Meaningful inner numbers (e.g. 'albinism type 1A') are preserved.
    """
    result = re.sub(r"\s+\d+$", "", name).strip()
    return result.rstrip(",")


def guess_pathology_tag(
    disease_name: str,
    rules: List[Tuple[re.Pattern, str]],
) -> Tuple[str, bool]:
    """
    Guess the canonical #pathology/ tag for a disease name.

    Returns (tag, is_confident). is_confident=False (with tag="") means no rule
    matched and the entry needs manual review. `rules` is the compiled list from
    load_pathology_rules(); first match wins.
    """
    name_stripped = strip_trailing_number(disease_name)
    for pattern, tag in rules:
        if pattern.search(name_stripped):
            return tag, True
    return "", False


def build_disease_lookup(
    gene_diseases: Dict[str, List[Dict]],
    rules: List[Tuple[re.Pattern, str]],
) -> Tuple[Dict, Dict, List[Dict]]:
    """
    Build the disease lookup table keyed by acronym.

    Args:
        gene_diseases: {gene_symbol: [disease_dict, ...]} as produced by
            query_all_genes / extract_diseases_from_uniprot_json.
        rules: compiled pathology-tag rules (load_pathology_rules()).

    Returns:
        (disease_lookup, pathology_tag_mapping, review_items)
          - disease_lookup: acronym -> {full_name, group_name, omim,
            pathology_tag, confident, genes}
          - pathology_tag_mapping: group_name -> {pathology_tag, confident,
            diseases, acronyms, genes, omim}
          - review_items: deduplicated list of uncertain (disease, gene) dicts
    """
    disease_lookup: Dict[str, Dict] = {}
    disease_name_index: Dict[str, Dict] = {}
    review_items: List[Dict] = []

    for gene_symbol, diseases in gene_diseases.items():
        for d in diseases:
            acronym = d.get("acronym", "")
            disease_name = d.get("disease_name", "")
            omim_id = d.get("omim_id", "")

            if not disease_name:
                continue

            tag, confident = guess_pathology_tag(disease_name, rules)
            group_name = strip_trailing_number(disease_name)

            if acronym:
                if acronym not in disease_lookup:
                    disease_lookup[acronym] = {
                        "full_name": disease_name,
                        "group_name": group_name,
                        "omim": omim_id,
                        "pathology_tag": tag,
                        "confident": confident,
                        "genes": [gene_symbol],
                    }
                else:
                    entry = disease_lookup[acronym]
                    if gene_symbol not in entry["genes"]:
                        entry["genes"].append(gene_symbol)
                    if omim_id and not entry["omim"]:
                        entry["omim"] = omim_id

            if disease_name not in disease_name_index:
                disease_name_index[disease_name] = {
                    "group_name": group_name,
                    "tag": tag,
                    "confident": confident,
                    "acronyms": [acronym] if acronym else [],
                    "genes": [gene_symbol],
                    "omim": omim_id,
                }
            else:
                entry = disease_name_index[disease_name]
                if gene_symbol not in entry["genes"]:
                    entry["genes"].append(gene_symbol)
                if acronym and acronym not in entry["acronyms"]:
                    entry["acronyms"].append(acronym)

            if not confident:
                review_items.append({
                    "disease_name": disease_name,
                    "acronym": acronym,
                    "gene": gene_symbol,
                    "omim": omim_id,
                    "suggested_tag": tag,
                })

    pathology_tag_mapping: Dict[str, Dict] = {}
    for disease_name, info in disease_name_index.items():
        group = info["group_name"]
        if group not in pathology_tag_mapping:
            pathology_tag_mapping[group] = {
                "pathology_tag": info["tag"],
                "confident": info["confident"],
                "diseases": [disease_name],
                "acronyms": list(info["acronyms"]),
                "genes": list(info["genes"]),
                "omim": info["omim"],
            }
        else:
            entry = pathology_tag_mapping[group]
            if disease_name not in entry["diseases"]:
                entry["diseases"].append(disease_name)
            for a in info["acronyms"]:
                if a and a not in entry["acronyms"]:
                    entry["acronyms"].append(a)
            for g in info["genes"]:
                if g not in entry["genes"]:
                    entry["genes"].append(g)

    seen_review = set()
    unique_review: List[Dict] = []
    for item in review_items:
        key = (item["disease_name"], item["gene"])
        if key not in seen_review:
            seen_review.add(key)
            unique_review.append(item)

    return disease_lookup, pathology_tag_mapping, unique_review


# ═══════════════════════════════════════════════════════════════════════════
# HTTP fetchers
# ═══════════════════════════════════════════════════════════════════════════

@retry_on_failure(max_retries=2, base_delay=2.0)
def _http_get(url: str, params: Optional[dict] = None, timeout: int = REQUEST_TIMEOUT):
    """GET with retry/backoff on transient failures."""
    return requests.get(url, params=params, timeout=timeout)


def fetch_uniprot_diseases(uniprot_id: str) -> Optional[Dict]:
    """Fetch the full UniProt entry JSON for a single accession (or None)."""
    url = UNIPROT_API_URL.format(uniprot_id)
    try:
        resp = _http_get(url)
    except requests.exceptions.RequestException:
        return None
    if resp.status_code == 200:
        return resp.json()
    return None


def fetch_batch_diseases(accession_map: Dict[str, str]) -> Dict[str, List[Dict]]:
    """
    Fetch disease data for a batch of UniProt accessions via the search API.

    accession_map: {uniprot_accession: gene_symbol}
    Returns: {gene_symbol: [disease_dict, ...]}. Genes whose accession is absent
    from the response are returned with an empty list.
    """
    if not accession_map:
        return {}

    accessions = list(accession_map.keys())
    query = " OR ".join(f"accession:{a}" for a in accessions)
    params = {
        "query": query,
        "fields": "accession,gene_names,cc_disease",
        "format": "json",
        "size": 500,
    }

    try:
        resp = _http_get(SEARCH_URL, params=params, timeout=60)
    except requests.exceptions.RequestException:
        return {}
    if resp.status_code != 200:
        return {}

    data = resp.json()
    results_list = data.get("results", [])

    batch_results: Dict[str, List[Dict]] = {}
    seen_accessions = set()

    for entry in results_list:
        acc = entry.get("primaryAccession", "")
        seen_accessions.add(acc)
        gene_symbol = accession_map.get(acc, "")
        if not gene_symbol:
            continue
        batch_results[gene_symbol] = extract_diseases_from_uniprot_json(entry)

    for acc, symbol in accession_map.items():
        if acc not in seen_accessions and symbol not in batch_results:
            batch_results[symbol] = []

    return batch_results


def query_all_genes(
    gene_symbols: List[str],
    hgnc_index: Dict[str, Dict],
    cache: Optional[Dict[str, Any]] = None,
    skip_api: bool = False,
    batch_delay: float = 1.0,
) -> Dict[str, List[Dict]]:
    """
    Query UniProt for disease data for all genes using batch search calls.

    Returns {upper(gene_symbol): [disease_dict, ...]}. `cache` (if given) is read
    and updated in place to avoid redundant API calls; pass skip_api=True to use
    cache only. `batch_delay` is the inter-batch courtesy pause in seconds.
    """
    if cache is None:
        cache = {}

    results: Dict[str, List[Dict]] = {}
    pending_accession_map: Dict[str, str] = {}  # uniprot_id -> gene_symbol

    for symbol in gene_symbols:
        symbol_upper = symbol.upper()

        if symbol_upper in cache:
            results[symbol_upper] = cache[symbol_upper]
            continue

        if skip_api:
            continue

        hgnc_entry = hgnc_index.get(symbol_upper)
        if not hgnc_entry:
            cache[symbol_upper] = []
            results[symbol_upper] = []
            continue

        uniprot_id = get_uniprot_id(hgnc_entry)
        if not uniprot_id:
            cache[symbol_upper] = []
            results[symbol_upper] = []
            continue

        pending_accession_map[uniprot_id] = symbol_upper

    accession_items = list(pending_accession_map.items())
    total_batches = (len(accession_items) + BATCH_SIZE - 1) // BATCH_SIZE

    for batch_idx in range(total_batches):
        start = batch_idx * BATCH_SIZE
        end = min(start + BATCH_SIZE, len(accession_items))
        batch = dict(accession_items[start:end])

        batch_results = fetch_batch_diseases(batch)

        if batch_results:
            for symbol, diseases in batch_results.items():
                results[symbol] = diseases
                cache[symbol] = diseases
        else:
            for acc, symbol in batch.items():
                cache[symbol] = []
                results[symbol] = []

        if batch_idx < total_batches - 1:
            time.sleep(batch_delay)

    return results
