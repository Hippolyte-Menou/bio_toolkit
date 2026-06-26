"""VariaICO variant-coordinate lookup client.

Ported from the vault's _assets/code/varaico/query_varaico.py. That script
mapped gene symbols to hg38 regions via the UCSC refGene table, then queried the
VariaICO bigBed files (varaico.bb / varaicoSuppl.bb) for literature-extracted
variants overlapping each gene.

The API-access layer preserved here is the UCSC refGene download, which the
original ran via a `curl` subprocess and which is ported to `requests` +
`retry_on_failure` per the package's HTTP convention. The bigBed reading is a
local-file operation via `pybigtools`; it is imported lazily so this module
imports cleanly in environments without pybigtools (e.g. the test env, which
mocks the reader).

Methods RETURN parsed data (coordinate dicts, variant dicts). No TSV/JSON
export, no dashboard rendering, no argparse CLI — those were vault-side
orchestration in the original.

    from bio_toolkit.clients.varaico import VaraicoClient

    client = VaraicoClient(varaico_bb="…/varaico.bb")
    coords = client.load_coordinates()           # {symbol: {chrom,start,end}}
    variants = client.query_gene_symbol("PAX6")  # list[dict]
"""

import gzip
import io
import json
import logging
from datetime import date
from pathlib import Path

import requests

from bio_toolkit.util.retry import retry_on_failure

logger = logging.getLogger(__name__)

REFGENE_URL = (
    "https://hgdownload.soe.ucsc.edu/goldenPath/hg38/database/refGene.txt.gz"
)
PADDING = 5000  # bp padding around gene boundaries
MAX_YEAR = date.today().year + 5

# Compact fields for a per-gene dashboard view.
DASHBOARD_FIELDS = [
    "cdot", "pdot", "effect", "ref", "alt",
    "pmid", "year", "author", "title", "doi", "articlesCount",
]

# Structured output fields (full record).
OUTPUT_FIELDS = [
    "chrom", "chromStart", "chromEnd", "gene", "variant_name",
    "hgncSymbol", "refseq", "cdot", "pdot", "effect",
    "ref", "alt", "pmid", "author", "title", "year", "journal",
    "doi", "articlesCount", "textSection", "source", "outlink",
]


@retry_on_failure(max_retries=3, base_delay=1.0)
def _download_refgene_gz(url: str = REFGENE_URL) -> bytes:
    """Download the UCSC refGene .txt.gz and return the raw gzip bytes.

    Ported from the curl subprocess in the original; uses requests with
    streaming and retry_on_failure for transient errors.
    """
    logger.info(f"Downloading UCSC refGene coordinates from {url}")
    resp = requests.get(url, timeout=300)
    resp.raise_for_status()
    return resp.content


def parse_refgene_coordinates(gz_bytes: bytes) -> dict:
    """Parse refGene .txt.gz bytes into {symbol: {chrom, start, end}}.

    Merges multiple transcripts of the same symbol on the same chromosome by
    taking the min start / max end (the original's accumulation logic).
    Skips alt/random contigs (chrom names containing '_').
    """
    coords: dict = {}
    with gzip.GzipFile(fileobj=io.BytesIO(gz_bytes)) as f:
        for raw in f:
            line = raw.decode("utf-8", "replace")
            parts = line.rstrip("\n").split("\t")
            if len(parts) < 13:
                continue
            chrom = parts[2]
            if "_" in chrom:
                continue
            symbol = parts[12]  # name2 = gene symbol
            s, e = int(parts[4]), int(parts[5])

            if symbol not in coords:
                coords[symbol] = {"chrom": chrom, "start": s, "end": e}
            else:
                existing = coords[symbol]
                if existing["chrom"] == chrom:
                    existing["start"] = min(existing["start"], s)
                    existing["end"] = max(existing["end"], e)
    logger.info(f"Parsed {len(coords)} gene coordinates")
    return coords


def parse_record(chrom: str, rec) -> dict | None:
    """Parse a pybigtools bigBed record tuple into a structured dict.

    pybigtools splits all whitespace-separated tokens into individual tuple
    elements, so author/title/journal/snippets span a variable number of
    indices. The fixed-position fields are:
      - Front (anchored by outlink URL scan): outlink, pmid, textSection,
        articlesCount
      - Back (counting from end): [-1]=doi [-2]=effect [-3]=pdot [-4]=cdot
               [-5]=refseq [-6]=hgncSymbol [-7]=alt [-8]=ref
    Everything in between is author+title+year+journal+snippets.

    Ported verbatim from the original.
    """
    if len(rec) < 20:
        return None

    start, end = int(rec[0]), int(rec[1])
    gene = rec[2]
    variant_name = rec[3]

    # Anchor on the outlink URL to handle records with shifted fields
    outlink_idx = None
    for i in range(7, min(15, len(rec))):
        if rec[i].startswith("https://varaico.com"):
            outlink_idx = i
            break
    if outlink_idx is None:
        return None  # can't reliably parse without the anchor

    outlink = rec[outlink_idx]
    pmid = rec[outlink_idx + 1]
    text_section = rec[outlink_idx + 2]
    articles_count = rec[outlink_idx + 3]
    middle_start = outlink_idx + 4  # first token after articlesCount

    if not pmid.isdigit():
        pmid = ""

    # Back fields (stable positions from the end)
    doi = rec[-1]
    effect = rec[-2]
    pdot = rec[-3]
    cdot = rec[-4]
    refseq = rec[-5]
    hgnc_symbol = rec[-6]
    alt = rec[-7]
    ref = rec[-8]

    # Middle zone: author, title, year, journal, snippets
    middle = rec[middle_start:-8]
    year = ""
    author_parts = []
    title_parts = []
    for i, token in enumerate(middle):
        if token.isdigit() and len(token) == 4 and 1950 <= int(token) <= MAX_YEAR:
            year = token
            author_parts = list(middle[:min(4, i)])
            title_parts = list(middle[4:i]) if i > 4 else []
            break

    return {
        "chrom": chrom,
        "chromStart": start,
        "chromEnd": end,
        "gene": gene,
        "variant_name": variant_name,
        "hgncSymbol": hgnc_symbol,
        "refseq": refseq,
        "cdot": cdot,
        "pdot": pdot,
        "effect": effect,
        "ref": ref,
        "alt": alt,
        "pmid": pmid,
        "articlesCount": articles_count,
        "textSection": text_section,
        "author": " ".join(author_parts),
        "title": " ".join(title_parts),
        "year": year,
        "journal": "",
        "outlink": outlink,
        "doi": doi,
        "source": "",
    }


class VaraicoClient:
    """Client for VariaICO bigBed variant lookup by gene symbol."""

    def __init__(
        self,
        varaico_bb: str | Path | None = None,
        varaico_suppl_bb: str | Path | None = None,
        coord_cache: str | Path | None = None,
        padding: int = PADDING,
    ):
        self.varaico_bb = Path(varaico_bb) if varaico_bb else None
        self.varaico_suppl_bb = Path(varaico_suppl_bb) if varaico_suppl_bb else None
        self.coord_cache = Path(coord_cache) if coord_cache else None
        self.padding = padding
        self._coords: dict | None = None

    # ------------------------------------------------------------------
    # Coordinates (UCSC refGene)
    # ------------------------------------------------------------------

    def download_gene_coordinates(self) -> dict:
        """Download + parse UCSC refGene coordinates, caching to disk if set."""
        gz_bytes = _download_refgene_gz()
        coords = parse_refgene_coordinates(gz_bytes)
        if self.coord_cache:
            with open(self.coord_cache, "w") as f:
                json.dump(coords, f)
        return coords

    def load_coordinates(self) -> dict:
        """Load gene coordinates from cache, else download. Memoised."""
        if self._coords is not None:
            return self._coords
        if self.coord_cache and Path(self.coord_cache).exists():
            with open(self.coord_cache) as f:
                self._coords = json.load(f)
        else:
            self._coords = self.download_gene_coordinates()
        return self._coords

    # ------------------------------------------------------------------
    # bigBed query
    # ------------------------------------------------------------------

    def _open_bb(self, path: str | Path):
        """Open a bigBed file via pybigtools (lazy import)."""
        import pybigtools
        return pybigtools.open(str(path))

    def query_region(self, bb, chrom: str, start: int, end: int,
                     gene_symbol: str) -> list[dict]:
        """Query an open bigBed handle for variants in a region.

        Keeps only records whose hgncSymbol matches gene_symbol
        (case-insensitive), parsed via parse_record. Ported from query_gene().
        """
        results: list[dict] = []
        try:
            for rec in bb.records(chrom, start, end):
                parsed = parse_record(chrom, rec)
                if parsed and parsed["hgncSymbol"].upper() == gene_symbol.upper():
                    results.append(parsed)
        except Exception as e:
            logger.warning(f"error querying {chrom}:{start}-{end}: {e}")
        return results

    def query_gene_symbol(self, gene_symbol: str, suppl: bool = False) -> list[dict]:
        """Resolve a gene symbol to its region and query the bigBed file(s).

        Returns a flat list of variant dicts (with a 'source' of "main" or
        "suppl"). Genes with no coordinates yield an empty list.
        """
        gene = gene_symbol.upper()
        coords = self.load_coordinates()
        if gene not in coords:
            logger.info(f"{gene}: no coordinates found")
            return []

        c = coords[gene]
        chrom = c["chrom"]
        start = max(0, c["start"] - self.padding)
        end = c["end"] + self.padding

        handles = []
        if self.varaico_bb:
            handles.append(("main", self._open_bb(self.varaico_bb)))
        if suppl and self.varaico_suppl_bb and Path(self.varaico_suppl_bb).exists():
            handles.append(("suppl", self._open_bb(self.varaico_suppl_bb)))

        all_results: list[dict] = []
        for source_name, bb in handles:
            results = self.query_region(bb, chrom, start, end, gene)
            for r in results:
                r["source"] = source_name
            all_results.extend(results)
        return all_results
