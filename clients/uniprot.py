"""UniProt / EBI Proteins API client.

Look up a protein by UniProt accession (or by gene symbol via HGNC) and return
the raw UniProtKB JSON plus the parsed core protein fields.

Ported from the vault `gene-generation` package:
  * `gene_data_fetcher.py` — `query_uniprot_api` (UniProtKB REST
    `rest.uniprot.org/uniprotkb/{acc}.json`) and `query_ebi_api` (the EBI
    Proteins / InterPro / AlphaFold endpoints), plus the
    `UniProtFetcher.fetch_gene_data` orchestration that assembles
    `{basic_data, additional_data}`. The endpoint table + timeouts come from
    `settings.APIConfig`.
  * `uniprot_processor.py` — `UniProtDataProcessor`, the pure parser over that
    blob. The data-fetching-relevant extractors (protein name/alias, sequence,
    nomenclature, genomic coordinates, comments, keywords) are ported as
    free functions returning raw parsed data.

This module is the data-fetching half only. Domain-specific assembly of the
gene note (the `EnhancedGeneInfo` class, SVG diagrams, markdown rendering) stays
in the caller. Functions RETURN parsed data; they do not render.
"""

import logging

import requests

from bio_toolkit.util.retry import retry_on_failure

logger = logging.getLogger(__name__)

# UniProtKB entry as JSON.
UNIPROT_BASE_URL = "https://rest.uniprot.org/uniprotkb/{}.json"

# EBI Proteins / InterPro / AlphaFold endpoints (ported from settings.APIConfig).
EBI_ENDPOINTS = {
    "variation": "https://www.ebi.ac.uk/proteins/api/variation/{}?format=json",
    "mutagenesis": "https://www.ebi.ac.uk/proteins/api/mutagenesis/{}?format=json",
    "coordinates": "https://www.ebi.ac.uk/proteins/api/coordinates/{}?format=json",
    "ptm": "https://www.ebi.ac.uk/proteins/api/proteomics/ptm/{}?format=json",
    "hpp": "https://www.ebi.ac.uk/proteins/api/proteomics/hpp/{}?format=json",
    "interpro": (
        "https://www.ebi.ac.uk/interpro/api/entry/all/protein/uniprot/{}"
        "?format=json&page_size=100&type=domain"
    ),
    "alphafold": "https://alphafold.ebi.ac.uk/files/AF-{}-F1-aa-substitutions.csv",
}

TIMEOUT = 10


# --- HTTP fetchers (ported from gene_data_fetcher.query_uniprot_api / query_ebi_api) ---

@retry_on_failure(max_retries=3, base_delay=1.0)
def _get(url: str) -> requests.Response:
    """GET a URL with a timeout, retrying on transient failures."""
    return requests.get(url, timeout=TIMEOUT)


def fetch_uniprotkb(accession: str) -> dict:
    """Fetch the UniProtKB entry JSON for a single accession.

    Returns ``{'success': True, 'data': <json>}`` on HTTP 200, else
    ``{'success': False, 'uniprot_id': accession, 'error': 'HTTP <code>'}``.
    Mirrors `gene_data_fetcher.query_uniprot_api`.
    """
    try:
        response = _get(UNIPROT_BASE_URL.format(accession))
    except requests.exceptions.RequestException as exc:
        return {"success": False, "uniprot_id": accession, "error": str(exc)}

    if response.status_code == 200:
        return {"success": True, "data": response.json()}
    return {"success": False, "uniprot_id": accession, "error": f"HTTP {response.status_code}"}


def fetch_ebi(data_type: str, accession: str) -> dict:
    """Fetch one EBI Proteins / InterPro / AlphaFold endpoint for an accession.

    `data_type` is a key of `EBI_ENDPOINTS`. CSV endpoints (AlphaFold) return raw
    text; the rest return parsed JSON. Mirrors `gene_data_fetcher.query_ebi_api`.
    """
    endpoint_url = EBI_ENDPOINTS[data_type]
    url = endpoint_url.format(accession)
    try:
        response = _get(url)
    except Exception:
        return {"success": False, "type": data_type, "error": "Request failed"}

    if response.status_code == 200:
        if url.endswith(".csv"):
            return {"success": True, "data": response.text, "type": data_type}
        return {"success": True, "data": response.json(), "type": data_type}
    return {"success": False, "type": data_type, "error": f"HTTP {response.status_code}"}


def fetch_protein(accession: str, include_ebi: bool = True) -> dict:
    """Fetch the full protein record for a UniProt accession.

    Returns a dict shaped like the vault's `uniprot_data` blob:

        {
            'uniprot_id': accession,
            'basic_data': <UniProtKB JSON>,        # or the error dict on failure
            'additional_data': {                   # only when include_ebi=True
                'variation': ..., 'coordinates': ..., 'interpro': ..., ...
            },
        }

    Mirrors `UniProtFetcher.fetch_gene_data` minus the HGNC-entry plumbing and
    the inter-request sleeps (rate limiting is the caller's concern). Failed EBI
    endpoints are stored as ``None``.
    """
    entry: dict = {"uniprot_id": accession}

    basic = fetch_uniprotkb(accession)
    entry["basic_data"] = basic["data"] if basic["success"] else basic

    if include_ebi:
        additional: dict = {}
        for data_type in EBI_ENDPOINTS:
            result = fetch_ebi(data_type, accession)
            additional[data_type] = result["data"] if result["success"] else None
        entry["additional_data"] = additional

    return entry


# --- parsers (ported from uniprot_processor.UniProtDataProcessor) ---

def _safe_get(d, *keys, default=None):
    """Safely traverse nested dicts. Returns default if any step is None or not a dict.

    Ported verbatim from `uniprot_processor._safe_get`.
    """
    current = d
    for key in keys:
        if not isinstance(current, dict):
            return default
        current = current.get(key)
    return current if current is not None else default


def parse_protein_info(basic_data: dict) -> dict:
    """Extract the core protein fields from a UniProtKB entry JSON.

    `basic_data` is the UniProtKB JSON (the `basic_data` of `fetch_protein`).
    Returns name, alias, sequence, length and molecular weight. Ported from
    `UniProtDataProcessor.get_protein_info` / `_get_protein_name_uniprot` /
    `_get_protein_alias`.
    """
    sequence = _safe_get(basic_data, "sequence", default={})

    alt_names = _safe_get(basic_data, "proteinDescription", "alternativeNames", default=[])
    protein_alias = ""
    if alt_names:
        protein_alias = alt_names[0].get("fullName", {}).get("value", "")

    return {
        "protein_name": _safe_get(
            basic_data, "proteinDescription", "recommendedName", "fullName", "value", default=""
        ),
        "protein_alias": protein_alias,
        "protein_sequence": sequence.get("value", ""),
        "protein_size_aa": sequence.get("length", 0),
        "molecular_weight": sequence.get("molWeight", 0),
    }


def parse_protein_nomenclature(basic_data: dict) -> dict:
    """Extract the full protein nomenclature from a UniProtKB entry JSON.

    Recommended full name + short names, alternative names, and any flag
    (e.g. "Precursor"). Ported from
    `UniProtDataProcessor.get_protein_nomenclature` (minus the HGNC
    `prev_symbol` merge, which is HGNC data and lives in the hgnc client).
    """
    protein_desc = _safe_get(basic_data, "proteinDescription", default={})

    recommended = protein_desc.get("recommendedName", {})
    full_name = recommended.get("fullName", {}).get("value", "")
    short_names = [sn.get("value", "") for sn in recommended.get("shortNames", [])]

    alternative_names = []
    for alt_name in protein_desc.get("alternativeNames", []):
        alt_full = alt_name.get("fullName", {}).get("value", "")
        alt_shorts = [sn.get("value", "") for sn in alt_name.get("shortNames", [])]
        if alt_full:
            alternative_names.append({"fullName": alt_full, "shortNames": alt_shorts})

    return {
        "recommended_full_name": full_name,
        "recommended_short_names": short_names,
        "alternative_names": alternative_names,
        "flag": protein_desc.get("flag", ""),
    }


def parse_genomic_coordinates(basic_data: dict):
    """Extract (chromosome, start, end, assembly) from a UniProtKB coordinates blob.

    Returns a 4-tuple or None. Ported from
    `UniProtDataProcessor.get_genomic_coordinates`. Note: in the vault the
    coordinates live under `additional_data.coordinates`; here the caller passes
    whichever blob actually carries `gnCoordinate`.
    """
    gn_coordinate = _safe_get(basic_data, "coordinates", "gnCoordinate", default=[])
    if not gn_coordinate:
        return None

    genomic_location = gn_coordinate[0].get("genomicLocation", {})
    if not genomic_location:
        return None

    return (
        genomic_location["chromosome"],
        genomic_location["start"],
        genomic_location["end"],
        genomic_location["assemblyName"],
    )


def lookup_protein(accession: str, include_ebi: bool = True) -> dict:
    """Fetch a protein and return raw + parsed core fields in one call.

    Returns:
        {
            'accession': accession,
            'raw': <fetch_protein result>,
            'protein_info': <parse_protein_info>,
            'nomenclature': <parse_protein_nomenclature>,
        }

    Returns parsed data only — no rendering. If the UniProtKB fetch failed,
    `protein_info` / `nomenclature` parse over an empty dict (all-default
    values) and the failure detail stays in `raw['basic_data']`.
    """
    raw = fetch_protein(accession, include_ebi=include_ebi)
    basic = raw.get("basic_data", {})
    if not isinstance(basic, dict) or basic.get("success") is False:
        basic = {}

    return {
        "accession": accession,
        "raw": raw,
        "protein_info": parse_protein_info(basic),
        "nomenclature": parse_protein_nomenclature(basic),
    }
