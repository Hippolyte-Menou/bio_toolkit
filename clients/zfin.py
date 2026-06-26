"""
ZFIN (Zebrafish Information Network) client.

Ported from the MAC project's `Genes of Interest/scripts/fetch_apis.py`
(`fetch_zfin` + the `_zfin_*` / `_ensure_zfin_indexes` machinery). The original
filtered phenotype/expression rows down to ocular keywords and wrote a text
file for MAC gene assessment; this shared client strips that filtering and
returns the RAW ortholog / phenotype / expression rows.

ZFIN has no per-gene REST API: it publishes tab-separated bulk evidence files
at https://zfin.org/downloads . We cache three files and index them in memory:

  human_orthos.txt              human symbol -> ZDB gene id + zebrafish symbol
  phenoGeneCleanData_fish.txt   gene-phenotype associations (by ZDB id)
  wildtype-expression_fish.txt  wildtype expression per gene (by ZDB id)

Files are refreshed if older than ZFIN_CACHE_MAX_AGE_DAYS. The indexes are
built lazily and memoised at module level.

Public API:
    fetch_zfin(gene, cache_dir=None) -> dict
"""

from __future__ import annotations

import datetime as _dt
from pathlib import Path
from typing import Any

import requests

from bio_toolkit.util.retry import retry_on_failure

ZFIN_CACHE_MAX_AGE_DAYS = 30

ZFIN_FILES = {
    "human_orthos": "https://zfin.org/downloads/human_orthos.txt",
    "phenotype":    "https://zfin.org/downloads/phenoGeneCleanData_fish.txt",
    "expression":   "https://zfin.org/downloads/wildtype-expression_fish.txt",
}

# Default cache location: alongside this module's package, under .zfin_cache/.
# Callers can override via the cache_dir argument to fetch_zfin().
DEFAULT_CACHE_DIR = Path(__file__).resolve().parent / ".zfin_cache"

# Memoised indexes, keyed by resolved cache directory so distinct caches don't
# collide (the original used a single module-level dict for its one cache).
_ZFIN_INDEXES: dict[Path, dict[str, Any]] = {}


def _cache_path(cache_dir: Path, name: str) -> Path:
    return cache_dir / f"{name}.txt"


def _cache_stale(path: Path) -> bool:
    if not path.exists():
        return True
    age = _dt.datetime.now() - _dt.datetime.fromtimestamp(path.stat().st_mtime)
    return age > _dt.timedelta(days=ZFIN_CACHE_MAX_AGE_DAYS)


@retry_on_failure(max_retries=2, base_delay=2.0)
def _download_one(url: str, dest: Path) -> None:
    tmp = dest.with_suffix(".txt.partial")
    with requests.get(url, timeout=120, stream=True) as r:
        r.raise_for_status()
        with tmp.open("wb") as fh:
            for chunk in r.iter_content(chunk_size=1 << 15):
                if chunk:
                    fh.write(chunk)
    tmp.replace(dest)


def _download_zfin_files(cache_dir: Path) -> None:
    cache_dir.mkdir(parents=True, exist_ok=True)
    for name, url in ZFIN_FILES.items():
        dest = _cache_path(cache_dir, name)
        if not _cache_stale(dest):
            continue
        _download_one(url, dest)


def _build_indexes(cache_dir: Path) -> dict[str, Any]:
    # human_orthos.txt columns (observed):
    #   0 ZDB gene id   1 zebrafish symbol   2 zebrafish name   3 human symbol
    #   4 human name    5 OMIM   6 HGNC id   7 NCBI gene id     8 evidence code
    #   9 ZFIN pub id  10 evidence desc  11 ECO code  12 ECO description
    human_to_zdb: dict[str, list[dict[str, str]]] = {}
    with _cache_path(cache_dir, "human_orthos").open(encoding="utf-8") as fh:
        for line in fh:
            parts = line.rstrip("\n").split("\t")
            if len(parts) < 9:
                continue
            zdb, zf_sym, zf_name, human_sym = parts[0], parts[1], parts[2], parts[3]
            evidence = parts[8]
            pub = parts[9] if len(parts) > 9 else ""
            key = human_sym.strip().upper()
            bucket = human_to_zdb.setdefault(key, [])
            # de-dup: keep one entry per (zdb, zf_sym, evidence)
            sig = (zdb, zf_sym, evidence)
            if not any((b["zdb"], b["zf_sym"], b["evidence"]) == sig for b in bucket):
                bucket.append({
                    "zdb": zdb, "zf_sym": zf_sym, "zf_name": zf_name,
                    "evidence": evidence, "pub": pub,
                })

    # phenoGeneCleanData_fish.txt (observed columns):
    #   2 ZDB gene id  7 structure id  8 structure name  9 quality id
    #  10 quality name 11 tag  18 fish id  19 fish name  20/21 stages  23 pub
    phenos_by_zdb: dict[str, list[dict[str, str]]] = {}
    with _cache_path(cache_dir, "phenotype").open(encoding="utf-8") as fh:
        for line in fh:
            parts = line.rstrip("\n").split("\t")
            if len(parts) < 12:
                continue
            zdb = parts[2]
            phenos_by_zdb.setdefault(zdb, []).append({
                "structure_id":   parts[7],
                "structure_name": parts[8],
                "quality_id":     parts[9],
                "quality_name":   parts[10],
                "tag":            parts[11],
                "fish_id":        parts[18] if len(parts) > 18 else "",
                "fish_name":      parts[19] if len(parts) > 19 else "",
                "start_stage":    parts[20] if len(parts) > 20 else "",
                "end_stage":      parts[21] if len(parts) > 21 else "",
                "pub":            parts[23] if len(parts) > 23 else "",
            })

    # wildtype-expression_fish.txt (observed columns):
    #   0 ZDB gene id  2 genotype  3 anatomy id  4 anatomy name
    #   7 start stage  8 end stage  9 assay  11 pub id
    expr_by_zdb: dict[str, list[dict[str, str]]] = {}
    with _cache_path(cache_dir, "expression").open(encoding="utf-8") as fh:
        for line in fh:
            parts = line.rstrip("\n").split("\t")
            if len(parts) < 9:
                continue
            zdb = parts[0]
            expr_by_zdb.setdefault(zdb, []).append({
                "genotype":     parts[2],
                "anatomy_id":   parts[3],
                "anatomy_name": parts[4],
                "start_stage":  parts[7] if len(parts) > 7 else "",
                "end_stage":    parts[8] if len(parts) > 8 else "",
                "assay":        parts[9] if len(parts) > 9 else "",
                "pub":          parts[11] if len(parts) > 11 else "",
            })

    return {
        "human_to_zdb": human_to_zdb,
        "phenos_by_zdb": phenos_by_zdb,
        "expr_by_zdb": expr_by_zdb,
    }


def _ensure_indexes(cache_dir: Path) -> dict[str, Any]:
    cached = _ZFIN_INDEXES.get(cache_dir)
    if cached:
        return cached
    _download_zfin_files(cache_dir)
    idx = _build_indexes(cache_dir)
    _ZFIN_INDEXES[cache_dir] = idx
    return idx


def fetch_zfin(gene: str, cache_dir: str | Path | None = None) -> dict[str, Any]:
    """
    Fetch raw ZFIN ortholog / phenotype / expression data for a human `gene`.

    The three ZFIN bulk files are downloaded into `cache_dir` (default
    DEFAULT_CACHE_DIR) on first use and reused until older than
    ZFIN_CACHE_MAX_AGE_DAYS. Indexes are memoised per cache directory.

    Returns a dict:
        {
          "gene": str,                 # upper-cased
          "orthologs": list[dict],     # zebrafish ortholog rows (may be empty)
          "phenotypes": list[dict],    # all phenotype rows across ortholog ZDBs
          "expression": list[dict],    # all expression rows across ortholog ZDBs
        }

    No ocular keyword filtering is applied -- all rows for the gene's zebrafish
    orthologs are returned raw.
    """
    gene = gene.upper()
    resolved = Path(cache_dir) if cache_dir is not None else DEFAULT_CACHE_DIR
    idx = _ensure_indexes(resolved)

    orthos = idx["human_to_zdb"].get(gene, [])
    uniq_zdbs = sorted({o["zdb"] for o in orthos})

    all_phenos: list[dict[str, str]] = []
    all_expr: list[dict[str, str]] = []
    for zdb in uniq_zdbs:
        all_phenos.extend(idx["phenos_by_zdb"].get(zdb, []))
        all_expr.extend(idx["expr_by_zdb"].get(zdb, []))

    return {
        "gene": gene,
        "orthologs": orthos,
        "phenotypes": all_phenos,
        "expression": all_expr,
    }
