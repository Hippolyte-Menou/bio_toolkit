"""NCBI Gene client (E-utilities).

Resolve a human gene symbol to its NCBI Gene (Entrez) ID and return the parsed
gene summary: official symbol, full name, aliases, chromosome, map location,
and the free-text summary.

Dedup / source note:
  The vault `gene-generation` package does NOT ship a live NCBI Gene API client.
  Its NCBI-Gene touch points are:
    * `settings.GeneConfig.DATABASE_URLS["ncbi"]` =
      "https://www.ncbi.nlm.nih.gov/gene/{}" — a static link template keyed on
      the Entrez Gene ID.
    * the Entrez ID itself, carried through the HGNC record (`entrez_id`) that
      `gene_data_fetcher.HGNCDataLoader` loads from `hgnc_complete_set.txt`.
  `gene_data_fetcher.py` fetches from UniProt + EBI, not NCBI Gene.

  So the data-fetching half of "NCBI Gene" is reconstructed here as the
  canonical E-utilities lookup (esearch -> esummary), preserving the URL-builder
  behaviour (`gene_url`) that did exist. Request/parse behaviour follows the
  documented NCBI eutils JSON contract. Functions RETURN parsed data.
"""

import logging

import requests

from bio_toolkit.util.retry import retry_on_failure

logger = logging.getLogger(__name__)

EUTILS_BASE = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
ESEARCH_URL = f"{EUTILS_BASE}/esearch.fcgi"
ESUMMARY_URL = f"{EUTILS_BASE}/esummary.fcgi"

# Static link template ported from settings.GeneConfig.DATABASE_URLS["ncbi"].
GENE_URL_TEMPLATE = "https://www.ncbi.nlm.nih.gov/gene/{}"

DEFAULT_ORGANISM = "Homo sapiens"
TIMEOUT = 10


def gene_url(gene_id: str | int) -> str:
    """Build the public NCBI Gene web URL for an Entrez Gene ID."""
    return GENE_URL_TEMPLATE.format(gene_id)


@retry_on_failure(max_retries=3, base_delay=1.0)
def _get(url: str, params: dict) -> requests.Response:
    """GET an eutils endpoint as JSON. Retries on transient failures."""
    response = requests.get(url, params=params, timeout=TIMEOUT)
    response.raise_for_status()
    return response


def esearch_gene_id(symbol: str, organism: str = DEFAULT_ORGANISM) -> str | None:
    """Resolve a gene symbol to its NCBI Entrez Gene ID via esearch.

    Queries the gene database with ``<symbol>[sym] AND <organism>[orgn]`` and
    returns the first matching Gene ID, or None if nothing matched.
    """
    term = f"{symbol}[sym] AND {organism}[orgn]"
    response = _get(
        ESEARCH_URL,
        {"db": "gene", "term": term, "retmode": "json"},
    )
    id_list = response.json().get("esearchresult", {}).get("idlist", [])
    if not id_list:
        logger.warning("No NCBI Gene entry found for '%s' (%s)", symbol, organism)
        return None
    return id_list[0]


def esummary_gene(gene_id: str | int) -> dict | None:
    """Fetch the NCBI Gene summary document for an Entrez Gene ID and parse it.

    Returns a dict with keys:
        gene_id           Entrez Gene ID (str)
        symbol            official symbol (str)
        name              official full name (str)
        description       description / full name (str)
        aliases           other aliases, split from the comma list (list[str])
        chromosome        chromosome (str)
        map_location      cytogenetic map location (str)
        summary           free-text gene summary (str)
        url               public NCBI Gene URL (str)

    Returns None if the esummary result has no document for the ID.
    """
    gene_id = str(gene_id)
    response = _get(
        ESUMMARY_URL,
        {"db": "gene", "id": gene_id, "retmode": "json"},
    )
    result = response.json().get("result", {})
    doc = result.get(gene_id)
    if not doc:
        logger.warning("No NCBI Gene esummary document for ID '%s'", gene_id)
        return None

    other_aliases = doc.get("otheraliases", "")
    aliases = [a.strip() for a in other_aliases.split(",") if a.strip()]

    return {
        "gene_id": gene_id,
        "symbol": doc.get("name", ""),
        "name": doc.get("description", ""),
        "description": doc.get("description", ""),
        "aliases": aliases,
        "chromosome": doc.get("chromosome", ""),
        "map_location": doc.get("maplocation", ""),
        "summary": doc.get("summary", ""),
        "url": gene_url(gene_id),
    }


def fetch_gene(symbol: str, organism: str = DEFAULT_ORGANISM) -> dict | None:
    """Look up a gene by symbol and return its parsed NCBI Gene summary.

    Two-step E-utilities lookup: esearch the symbol to a Gene ID, then esummary
    that ID. Returns the parsed summary dict (see `esummary_gene`) or None if the
    symbol does not resolve to a Gene ID.
    """
    gene_id = esearch_gene_id(symbol, organism=organism)
    if gene_id is None:
        return None
    return esummary_gene(gene_id)
