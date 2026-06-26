"""HGNC (HUGO Gene Nomenclature Committee) client.

Resolve a gene symbol against the HGNC REST API and return the parsed
nomenclature record: approved symbol, alias symbols, previous symbols, full
name, and the common cross-reference IDs.

Ported / reconciled from two sources:
  * Zotero bot `genebot/hgnc.py` (`get_gene_aliases`) — the live REST call to
    `rest.genenames.org/fetch/symbol/{symbol}` and the
    response.docs[0] field extraction (symbol / alias_symbol / prev_symbol /
    name). That source returned a *flat set* of names for fuzzy matching.
  * Vault `gene-generation` — `UniProtDataProcessor.get_basic_gene_info` /
    `get_protein_nomenclature` document the HGNC record fields the downstream
    note-builder consumes (hgnc_id, ensembl_gene_id, refseq, prev_symbol, etc.).

Reconciliation (superset): `fetch_hgnc_record()` returns the full parsed record
as a dict so callers can pick fields. `get_gene_aliases()` is preserved as a
thin wrapper that flattens the record into the same set the bot returned, so the
old call site keeps working. Only the data-fetching half is here; any
domain-specific filtering / note rendering stays in the caller.
"""

import logging

import requests

from bio_toolkit.util.retry import retry_on_failure

logger = logging.getLogger(__name__)

# rest.genenames.org fetch endpoint — exact-match lookup on the approved symbol.
HGNC_FETCH_URL = "https://rest.genenames.org/fetch/symbol/{symbol}"
TIMEOUT = 10


@retry_on_failure(max_retries=3, base_delay=1.0)
def _fetch_symbol(symbol: str) -> requests.Response:
    """GET the HGNC fetch endpoint for one approved symbol. Retries on transient failures."""
    response = requests.get(
        HGNC_FETCH_URL.format(symbol=symbol),
        headers={"Accept": "application/json"},
        timeout=TIMEOUT,
    )
    response.raise_for_status()
    return response


def fetch_hgnc_record(symbol: str) -> dict | None:
    """Look up a gene symbol at HGNC and return its parsed nomenclature record.

    Returns a dict with keys:
        symbol           approved HGNC symbol (str)
        name             approved full gene name (str)
        alias_symbol     alias symbols (list[str])
        prev_symbol      previous symbols (list[str])
        hgnc_id          HGNC accession, e.g. "HGNC:8615" (str)
        entrez_id        NCBI Gene ID (str)
        ensembl_gene_id  Ensembl gene accession (str)
        refseq_accession RefSeq accession(s) (list[str])
        location         cytogenetic location (str)
        uniprot_ids      UniProt accession(s) (list[str])

    Returns None if HGNC has no entry for the symbol. List-valued HGNC fields
    are normalised to lists (HGNC omits the key when empty).
    """
    response = _fetch_symbol(symbol)
    docs = response.json().get("response", {}).get("docs", [])
    if not docs:
        logger.warning("No HGNC entry found for '%s'", symbol)
        return None

    doc = docs[0]
    return {
        "symbol": doc.get("symbol", ""),
        "name": doc.get("name", ""),
        "alias_symbol": list(doc.get("alias_symbol", [])),
        "prev_symbol": list(doc.get("prev_symbol", [])),
        "hgnc_id": doc.get("hgnc_id", ""),
        "entrez_id": doc.get("entrez_id", ""),
        "ensembl_gene_id": doc.get("ensembl_gene_id", ""),
        "refseq_accession": list(doc.get("refseq_accession", [])),
        "location": doc.get("location", ""),
        "uniprot_ids": list(doc.get("uniprot_ids", [])),
    }


def get_gene_aliases(symbol: str) -> set[str]:
    """Return a flat set of names/aliases for a gene symbol.

    Includes the approved symbol, alias symbols, previous symbols, and the full
    name. Preserves the bot's `genebot.hgnc.get_gene_aliases` contract: on a
    missing entry it returns ``{symbol}`` (the input symbol as-is) rather than an
    empty set, so the symbol always resolves to at least itself.
    """
    record = fetch_hgnc_record(symbol)
    if record is None:
        return {symbol}

    aliases: set[str] = set()
    aliases.add(record["symbol"] or symbol)
    aliases.update(record["alias_symbol"])
    aliases.update(record["prev_symbol"])
    if record["name"]:
        aliases.add(record["name"])

    logger.info("HGNC aliases for %s: %s", symbol, aliases)
    return aliases
