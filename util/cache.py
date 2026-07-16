"""
Resolvers for the shared, disease-blind substrate folders.

`literature/` is a top-level sibling of `lab/` under the Genetics root; the
gene-evidence cache lives at `lab/genes/`. All are OneDrive-synced and never
git-tracked; clients write them, every project reads them. Locations are
env-overridable (handy for tests and CI).

    from bio_toolkit.util.cache import gene_cache_dir, literature_dir, pdf_corpus_dir
"""

import os
from pathlib import Path


def _genetics_root() -> Path:
    """Genetics workspace root. Override with GENETICS_ROOT; else inferred.

    This file lives at <root>/lab/tools/util/cache.py, so the root is four
    parents up (util -> tools -> lab -> Genetics).
    """
    env = os.environ.get("GENETICS_ROOT")
    if env:
        return Path(env)
    return Path(__file__).resolve().parents[3]


def literature_dir() -> Path:
    """Shared literature base dir = the Zotero Linked Attachment Base Directory.

    Override with LITERATURE_DIR. The actual PDF corpus lives one level down in
    `pdf_corpus_dir()` (literature/pdfs/); this base also parents the vault's
    converted-markdown tree and any future literature siblings.
    """
    env = os.environ.get("LITERATURE_DIR")
    return Path(env) if env else _genetics_root() / "literature"


def pdf_corpus_dir() -> Path:
    """Shared PDF corpus dir (literature/pdfs/). Override with PDF_CORPUS_DIR.

    This is where Zotero (via ZotMoov) parks the linked-attachment PDFs and where
    the bot writes new downloads — one folder, citekey-named, every project reads.
    Sits under literature_dir() so a single Zotero base directory covers both the
    PDFs and the converted-markdown tree.
    """
    env = os.environ.get("PDF_CORPUS_DIR")
    return Path(env) if env else literature_dir() / "pdfs"


def gene_cache_dir() -> Path:
    """Shared disease-blind gene-evidence cache dir (lab/genes/). Override with GENE_CACHE_DIR."""
    env = os.environ.get("GENE_CACHE_DIR")
    return Path(env) if env else _genetics_root() / "lab" / "genes"


def gene_api_data_dir() -> Path:
    """Raw API cache subtree (lab/genes/api_data/), grouped by source.

    Writers compose gene_api_data_dir() / <source> / f"{GENE}.txt". Follows the
    GENE_CACHE_DIR override so tests/CI relocate the whole cache in one place.
    """
    return gene_cache_dir() / "api_data"


def md_corpus_dir() -> Path:
    """Converted-markdown corpus dir (literature/mds/). Override with MD_CORPUS_DIR.

    Sibling of pdf_corpus_dir() under literature_dir(); Stage A writes it, the
    extractor subagents read it.
    """
    env = os.environ.get("MD_CORPUS_DIR")
    return Path(env) if env else literature_dir() / "mds"


def facts_dir() -> Path:
    """The literature-facts project + data dir (literature/facts/). Override with FACTS_DIR.

    Root of the self-contained project git repo; also holds the sqlite data file
    (gitignored within it). Sibling of pdfs/ and mds/.
    """
    env = os.environ.get("FACTS_DIR")
    return Path(env) if env else literature_dir() / "facts"


def facts_db_path() -> Path:
    """The SQLite fact/claim store (literature/facts/facts.sqlite). Override with FACTS_DB_PATH."""
    env = os.environ.get("FACTS_DB_PATH")
    return Path(env) if env else facts_dir() / "facts.sqlite"
