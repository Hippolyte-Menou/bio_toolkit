import pytest

from bio_toolkit.facts import FactStore


@pytest.fixture
def store(tmp_path):
    s = FactStore(tmp_path / "facts.sqlite")
    s.init_schema()
    yield s
    s.close()


def test_init_schema_creates_tables(store):
    names = {r["name"] for r in store.conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"papers", "facts", "claims", "assertions", "citations",
            "provenance_log", "runs", "fact_entities"} <= names


def test_upsert_paper_idempotent(store):
    pid = store.upsert_paper("2005_bredrup", title="X", year=2005, status="converted")
    pid2 = store.upsert_paper("2005_bredrup", journal="AJHG")
    assert pid == pid2
    row = store.get_paper(pid)
    assert row["title"] == "X"          # not clobbered by the second upsert
    assert row["journal"] == "AJHG"     # filled by the second upsert
    assert row["year"] == 2005


def test_insert_fact_dedupes_within_paper(store):
    pid = store.upsert_paper("p1")
    f1 = store.insert_fact(pid, "Decorin  is expressed  in cornea.", quote="q", section="gene")
    f2 = store.insert_fact(pid, "decorin is expressed in cornea.", quote="q2")  # same after normalize
    assert isinstance(f1, int)
    assert f2 is None                   # duplicate content_hash -> skipped
    assert len(store.facts_for_paper(pid)) == 1


def test_fts_search_roundtrip(store):
    pid = store.upsert_paper("p1")
    store.insert_fact(pid, "Mutations in RPGR cause X-linked retinitis pigmentosa.")
    store.commit()
    hits = store.search_facts("retinitis")
    assert len(hits) == 1


def test_claim_assertions_and_asserters_ordered_by_year(store):
    a = store.upsert_paper("a", year=2001)
    b = store.upsert_paper("b", year=2010)
    fa = store.insert_fact(a, "claim text a")
    fb = store.insert_fact(b, "claim text b")
    cid = store.insert_claim("canonical claim")
    store.add_assertion(cid, b, fb)
    store.add_assertion(cid, a, fa)
    asserters = store.claim_asserters(cid)
    assert [r["id"] for r in asserters] == [a, b]     # year ASC


def test_citation_chain_recursive(store):
    a = store.upsert_paper("a", year=2001)
    b = store.upsert_paper("b", year=2005)
    c = store.upsert_paper("c", year=2010)
    store.add_citation(c, b)     # c cites b
    store.add_citation(b, a)     # b cites a
    chain = store.citation_chain(c)
    reached = {r["paper_id"] for r in chain}
    assert reached == {c, b, a}  # transitively reaches the root


def test_context_manager_commits(tmp_path):
    path = tmp_path / "f.sqlite"
    with FactStore(path) as s:
        s.init_schema()
        s.upsert_paper("x")
    with FactStore(path) as s2:
        assert s2.get_paper_by_citekey("x") is not None
