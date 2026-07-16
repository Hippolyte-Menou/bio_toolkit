"""
Zotero group-library WRITE client (thin).

A self-contained subset of the zotero-bot's ZoteroGroupClient — just the pieces
assess-genes' write-back needs — so assess-genes depends only on bio_toolkit,
not on the nested zotero-bot repo. Group-sink only: writes go to the machine
group (ZOTERO_GROUP_ID), never the hand-curated personal library.

Dedup is against ACTIVE + TRASHED identifiers, so a paper the user deliberately
deleted is never re-added. Credentials come from bio_toolkit.config.

Requires the optional `pyzotero` dependency (`pip install -e ".[zotero]"` in tools/).
"""

from __future__ import annotations

import re
import time
import logging
from dataclasses import dataclass

from pyzotero import zotero

from bio_toolkit import config

logger = logging.getLogger(__name__)

_PMID_RE = re.compile(r"PMID:\s*(\d+)", re.IGNORECASE)
_TYPE_TAG_MAP = {"review": "review", "editorial": "editorial",
                 "letter": "letter", "erratum": "erratum"}


@dataclass(frozen=True)
class DedupBaseline:
    """Identifiers already represented in the group library (active + trashed)."""
    pmid_to_key: dict
    doi_to_key: dict
    trashed_pmids: frozenset = frozenset()
    trashed_dois: frozenset = frozenset()
    library_size: int = 0

    @property
    def existing_pmids(self) -> set:
        return set(self.pmid_to_key) | set(self.trashed_pmids)

    @property
    def existing_dois(self) -> set:
        return set(self.doi_to_key) | set(self.trashed_dois)


def _extract_pmid(data: dict) -> str:
    extra = data.get("extra", "") or ""
    m = _PMID_RE.search(extra)
    if m:
        return m.group(1)
    url = data.get("url", "") or ""
    m = re.search(r"pubmed\.ncbi\.nlm\.nih\.gov/(\d+)", url)
    return m.group(1) if m else ""


def _extract_doi(data: dict) -> str:
    doi = (data.get("DOI", "") or "").strip().lower()
    if doi:
        return doi.replace("https://doi.org/", "").replace("http://doi.org/", "")
    return ""


class ZoteroWriter:
    def __init__(self, group_id: str | None = None, api_key: str | None = None,
                 delay: float = 1.0):
        gid = group_id or config.ZOTERO_GROUP_ID
        key = api_key or config.zotero_api_key()
        self.zot = zotero.Zotero(gid, "group", key)
        self.delay = delay
        self._collection_cache: dict[tuple[str, str | None], str] = {}

    # --- baseline -----------------------------------------------------------

    def _all(self, method, **kw) -> list[dict]:
        """Paginate a listing endpoint (100/page)."""
        out: list[dict] = []
        start = 0
        while True:
            page = method(limit=100, start=start, **kw)
            out.extend(page)
            if len(page) < 100:
                break
            start += 100
        return out

    def get_dedup_baseline(self, *, include_trashed: bool = True) -> DedupBaseline:
        pmid_to_key: dict[str, str] = {}
        doi_to_key: dict[str, str] = {}
        active = self._all(self.zot.items, itemType="-attachment")
        for it in active:
            data = it.get("data", {})
            key = data.get("key", "")
            pmid, doi = _extract_pmid(data), _extract_doi(data)
            if pmid and key:
                pmid_to_key[pmid] = key
            if doi and key:
                doi_to_key[doi] = key
        trashed_pmids: set[str] = set()
        trashed_dois: set[str] = set()
        if include_trashed:
            for it in self._all(self.zot.trash):
                data = it.get("data", {})
                p, d = _extract_pmid(data), _extract_doi(data)
                if p:
                    trashed_pmids.add(p)
                if d:
                    trashed_dois.add(d)
        return DedupBaseline(
            pmid_to_key=pmid_to_key, doi_to_key=doi_to_key,
            trashed_pmids=frozenset(trashed_pmids), trashed_dois=frozenset(trashed_dois),
            library_size=len(active),
        )

    # --- collections --------------------------------------------------------

    def _load_collection_cache(self) -> None:
        self._collection_cache = {}
        for c in self.zot.everything(self.zot.collections()):
            name = c["data"]["name"]
            parent = c["data"].get("parentCollection") or None
            self._collection_cache[(name, parent)] = c["data"]["key"]

    def get_or_create_collection(self, name: str, parent_key: str | None = None) -> str:
        if not self._collection_cache:
            self._load_collection_cache()
        ck = (name, parent_key)
        if ck in self._collection_cache:
            return self._collection_cache[ck]
        payload: dict = {"name": name}
        if parent_key:
            payload["parentCollection"] = parent_key
        resp = self.zot.create_collections([payload])
        if not resp.get("successful"):
            raise RuntimeError(f"Failed to create collection '{name}': {resp}")
        key = list(resp["successful"].values())[0]["data"]["key"]
        self._collection_cache[ck] = key
        logger.info("Created Zotero collection '%s' -> %s", name, key)
        return key

    # --- items --------------------------------------------------------------

    def get_collection_pmids(self, collection_key: str) -> list[str]:
        pmids: list[str] = []
        for it in self._all(lambda **kw: self.zot.collection_items(collection_key, **kw),
                            itemType="-attachment"):
            pmid = _extract_pmid(it.get("data", {}))
            if pmid:
                pmids.append(pmid)
        return pmids

    def add_papers(self, records: list[dict], collection_key: str | None = None,
                   gene_symbol: str | None = None, extra_tags: list[str] | None = None,
                   source_tag: str = "source:assess-genes") -> dict:
        """Create journalArticle items in batches of 50. Records are work_to_record shape."""
        stats = {"added": 0, "failed": 0, "skipped_no_data": 0, "pmid_to_key": {}}
        items: list[dict] = []
        ordered_pmids: list[str] = []
        for r in records:
            if not r.get("title"):
                stats["skipped_no_data"] += 1
                continue
            tags: list[str] = []
            if gene_symbol:
                tags.append(gene_symbol)
            for pt in r.get("publication_type", []):
                if pt in _TYPE_TAG_MAP:
                    tags.append(_TYPE_TAG_MAP[pt])
            if extra_tags:
                tags.extend(extra_tags)
            if source_tag:
                tags.append(source_tag)
            tags.extend(r.get("mesh_terms", []))
            seen: set[str] = set()
            uniq = [{"tag": t} for t in tags if not (t in seen or seen.add(t))]
            item = {
                "itemType": "journalArticle",
                "title": r["title"],
                "abstractNote": r.get("abstract", ""),
                "publicationTitle": r.get("journal", ""),
                "journalAbbreviation": r.get("journal_abbr", ""),
                "volume": r.get("volume", ""), "issue": r.get("issue", ""),
                "pages": r.get("pages", ""), "date": r.get("date_published", ""),
                "DOI": r.get("doi", ""),
                "url": (f"https://pubmed.ncbi.nlm.nih.gov/{r['pmid']}/" if r.get("pmid") else ""),
                "extra": (f"PMID: {r['pmid']}" if r.get("pmid") else ""),
                "creators": [{"creatorType": "author", "firstName": a[0], "lastName": a[1]}
                             for a in r.get("authors", []) if isinstance(a, tuple)],
                "tags": uniq,
            }
            if collection_key:
                item["collections"] = [collection_key]
            items.append(item)
            ordered_pmids.append(r.get("pmid") or "")

        for i in range(0, len(items), 50):
            batch = items[i:i + 50]
            try:
                resp = self.zot.create_items(batch)
                stats["added"] += len(resp.get("successful", {}))
                stats["failed"] += len(resp.get("failed", {}))
                for idx_str, item_data in resp.get("successful", {}).items():
                    key = item_data.get("data", {}).get("key", "")
                    pmid = ordered_pmids[i + int(idx_str)]
                    if key and pmid:
                        stats["pmid_to_key"][pmid] = key
            except Exception as e:  # noqa: BLE001 - defer batch to next run
                logger.error("Zotero batch upload failed: %s", e)
                stats["failed"] += len(batch)
            time.sleep(self.delay)
        return stats

    def verify_upload(self, collection_key: str, expected_pmids: set[str],
                      label: str = "") -> set[str]:
        if not expected_pmids:
            return set()
        actual = set(self.get_collection_pmids(collection_key))
        missing = expected_pmids - actual
        if missing:
            logger.warning("%sverify_upload: %d/%d PMIDs missing: %s",
                           f"{label}: " if label else "", len(missing),
                           len(expected_pmids), sorted(missing))
        return missing
