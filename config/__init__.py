"""
Shared configuration for bio_toolkit: non-secret constants + secret accessors.

The Zotero group id is public (it's in the bot's CLAUDE.md); the API key is a
secret and comes from the environment or a gitignored local file — never tracked.
"""

import os
from pathlib import Path

# Public — the curated ophthalmo-genetics Zotero group library the bot writes to.
ZOTERO_GROUP_ID = "6432168"

# Canonical base URLs for the bio_toolkit clients (one place to repoint).
API_ENDPOINTS = {
    "hgnc": "https://rest.genenames.org",
    "uniprot": "https://rest.uniprot.org",
    "ebi_proteins": "https://www.ebi.ac.uk/proteins/api",
    "ncbi_eutils": "https://eutils.ncbi.nlm.nih.gov/entrez/eutils",
    "panelapp": "https://panelapp.genomicsengland.co.uk/api/v1",
    "mygene": "https://mygene.info/v3",
    "gwas_catalog": "https://www.ebi.ac.uk/gwas/rest/api",
    "openalex": "https://api.openalex.org",
    "hpa": "https://www.proteinatlas.org",
    "impc": "https://www.ebi.ac.uk/mi/impc/solr",
    "zfin": "https://zfin.org",
}

# UniProtKB entry-as-JSON template (one accession -> its UniProtKB record).
# Canonical home for the gene-note pipeline's protein endpoints (moved here from
# the vault's gene-generation/settings.APIConfig); the uniprot client reads these.
UNIPROT_KB_URL = "https://rest.uniprot.org/uniprotkb/{}.json"

# EBI Proteins / InterPro / AlphaFold per-data-type endpoint templates.
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


class MissingSecretError(RuntimeError):
    """Raised when a required secret is not configured."""


def zotero_api_key() -> str:
    """Return the Zotero API key.

    Looks first at the ZOTERO_API_KEY env var (used by CI), then a gitignored
    local file `tools/config/zotero_api_key.secret`. Raises MissingSecretError
    if neither is present — never returns an empty string silently.
    """
    key = os.environ.get("ZOTERO_API_KEY")
    if key:
        return key
    secret = Path(__file__).with_name("zotero_api_key.secret")
    if secret.exists():
        return secret.read_text(encoding="utf-8").strip()
    raise MissingSecretError(
        "Zotero API key not found. Set the ZOTERO_API_KEY environment variable, "
        "or create tools/config/zotero_api_key.secret (gitignored)."
    )
