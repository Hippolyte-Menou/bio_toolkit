"""
Resolvers for the shared, top-level, disease-blind substrate folders.

`literature/` and `gene-cache/` are siblings of `lab/` under the Genetics root.
They are OneDrive-synced and never git-tracked; clients write them, every project
reads them. Locations are env-overridable (handy for tests and CI).

    from bio_toolkit.util.cache import gene_cache_dir, literature_dir
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
    """Shared PDF corpus dir. Override with LITERATURE_DIR."""
    env = os.environ.get("LITERATURE_DIR")
    return Path(env) if env else _genetics_root() / "literature"


def gene_cache_dir() -> Path:
    """Shared disease-blind gene-evidence cache dir. Override with GENE_CACHE_DIR."""
    env = os.environ.get("GENE_CACHE_DIR")
    return Path(env) if env else _genetics_root() / "gene-cache"
