"""Unit tests for bio_toolkit.clients.zotero. pyzotero mocked, no network."""

from unittest.mock import patch

import pytest

from bio_toolkit.clients import zotero as zc


class FakeZot:
    """Stand-in for pyzotero.zotero.Zotero capturing the calls we make."""

    def __init__(self):
        self.library_id = "6432168"
        self._collections = []      # list of {"data": {...}}
        self._items = []            # active items
        self._trash = []            # trashed items
        self.created_items = []     # batches passed to create_items
        self._next_key = 1
        self.client = type("C", (), {"timeout": None})()

    # --- reads
    def collections(self):
        return list(self._collections)

    def everything(self, seq):
        return list(seq)

    def items(self, **kw):
        return list(self._items)

    def trash(self, **kw):
        return list(self._trash)

    def collection_items(self, key, **kw):
        return [i for i in self._items if key in i["data"].get("collections", [])]

    # --- writes
    def create_collections(self, payloads):
        key = f"COLL{self._next_key}"
        self._next_key += 1
        data = {"key": key, "name": payloads[0]["name"],
                "parentCollection": payloads[0].get("parentCollection", False)}
        self._collections.append({"data": data})
        return {"successful": {"0": {"data": data}}}

    def create_items(self, batch):
        self.created_items.append(batch)
        successful = {}
        for i, item in enumerate(batch):
            key = f"ITEM{self._next_key}"
            self._next_key += 1
            data = dict(item)
            data["key"] = key
            self._items.append({"key": key, "data": data})
            successful[str(i)] = {"data": data}
        return {"successful": successful, "failed": {}}


def _item(pmid=None, doi=None, trashed=False, collections=None):
    data = {"key": f"K{pmid or doi}", "collections": collections or []}
    if pmid:
        data["extra"] = f"PMID: {pmid}"
    if doi:
        data["DOI"] = doi
    return {"key": data["key"], "data": data}


@pytest.fixture
def writer():
    fake = FakeZot()
    with patch.object(zc.zotero, "Zotero", return_value=fake), \
            patch.object(zc.config, "zotero_api_key", return_value="fake"):
        w = zc.ZoteroWriter(group_id="6432168")
        w._fake = fake
        yield w


def test_dedup_baseline_bundles_active_and_trashed(writer):
    writer._fake._items = [_item(pmid="111"), _item(doi="10.1/x")]
    writer._fake._trash = [_item(pmid="999")]
    base = writer.get_dedup_baseline()
    assert "111" in base.existing_pmids
    assert "999" in base.existing_pmids        # trashed still blocks re-upload
    assert "10.1/x" in base.existing_dois
    assert base.library_size == 2              # active items only


def test_get_or_create_collection_is_idempotent(writer):
    k1 = writer.get_or_create_collection("MAC assess-genes")
    k2 = writer.get_or_create_collection("MAC assess-genes")
    assert k1 == k2
    assert len(writer._fake._collections) == 1   # created once, then cached


def test_add_papers_accounts_added_and_skips_untitled(writer):
    coll = writer.get_or_create_collection("MAC assess-genes")
    records = [
        {"pmid": "111", "title": "BMP7 coloboma", "authors": [("A", "B")],
         "mesh_terms": ["Coloboma"], "publication_type": ["article"]},
        {"pmid": "222", "title": "", "authors": []},   # untitled -> skipped
    ]
    stats = writer.add_papers(records, collection_key=coll, gene_symbol="BMP7")
    assert stats["added"] == 1
    assert stats["skipped_no_data"] == 1
    created = writer._fake.created_items[0][0]
    tags = {t["tag"] for t in created["tags"]}
    assert "BMP7" in tags and "source:assess-genes" in tags and "Coloboma" in tags


def test_verify_upload_reports_missing(writer):
    coll = writer.get_or_create_collection("MAC assess-genes")
    writer.add_papers([{"pmid": "111", "title": "x", "authors": []}],
                      collection_key=coll)
    missing = writer.verify_upload(coll, {"111", "333"})
    assert missing == {"333"}
