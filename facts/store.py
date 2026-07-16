"""FactStore — the one canonical SQLite access layer for literature-facts.

Standard library only (sqlite3). Owns connection management, schema init, and
typed read/write helpers. Contains NO scientific judgement (that lives in the
LLM subagents) — only deterministic persistence and graph queries.
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from bio_toolkit.util.cache import facts_db_path

DEFAULT_SCHEMA = Path(__file__).with_name("schema.sql")

_WS = re.compile(r"\s+")


def now_iso() -> str:
    """Current time as an ISO-8601 UTC string (no microseconds)."""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def content_hash(text: str) -> str:
    """Stable hash of a fact's normalised text (lowercase, whitespace-collapsed)."""
    norm = _WS.sub(" ", (text or "").strip().lower())
    return hashlib.sha256(norm.encode("utf-8")).hexdigest()


class FactStore:
    """Open, initialise, read, and write the fact/claim store."""

    def __init__(self, db_path: str | Path | None = None):
        self.db_path = Path(db_path) if db_path is not None else facts_db_path()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.db_path))
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.execute("PRAGMA journal_mode = WAL")   # reliably applied here (autocommit)

    # -- lifecycle ---------------------------------------------------------
    def init_schema(self, schema_path: str | Path = DEFAULT_SCHEMA) -> None:
        self.conn.executescript(Path(schema_path).read_text(encoding="utf-8"))
        self.conn.commit()

    def commit(self) -> None:
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> "FactStore":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if exc_type is None:
            self.conn.commit()
        else:
            self.conn.rollback()
        self.conn.close()

    # -- papers ------------------------------------------------------------
    _PAPER_COLS = ("openalex_id", "pmid", "doi", "title", "year", "journal",
                   "md_path", "in_corpus", "source", "status", "added_run_id")

    def upsert_paper(self, citekey: str, **fields) -> int:
        """Insert or update a paper by citekey. Existing non-null columns are
        preserved unless a non-null value is supplied (COALESCE semantics)."""
        cols = {k: v for k, v in fields.items() if k in self._PAPER_COLS}
        cols["updated_at"] = now_iso()
        keys = ["citekey"] + list(cols.keys())
        placeholders = ", ".join("?" for _ in keys)
        updates = ", ".join(f"{k}=COALESCE(excluded.{k}, {k})" for k in cols)
        sql = (f"INSERT INTO papers ({', '.join(keys)}) VALUES ({placeholders}) "
               f"ON CONFLICT(citekey) DO UPDATE SET {updates}")
        self.conn.execute(sql, [citekey] + list(cols.values()))
        row = self.conn.execute(
            "SELECT id FROM papers WHERE citekey = ?", (citekey,)).fetchone()
        return int(row["id"])

    def get_paper(self, paper_id: int) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM papers WHERE id = ?", (paper_id,)).fetchone()

    def get_paper_by_citekey(self, citekey: str) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM papers WHERE citekey = ?", (citekey,)).fetchone()

    def get_paper_by_openalex(self, openalex_id: str) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM papers WHERE openalex_id = ?", (openalex_id,)).fetchone()

    def papers_by_status(self, *statuses: str) -> list[sqlite3.Row]:
        marks = ", ".join("?" for _ in statuses)
        return self.conn.execute(
            f"SELECT * FROM papers WHERE status IN ({marks}) ORDER BY id",
            statuses).fetchall()

    def set_paper_status(self, paper_id: int, status: str, **fields) -> None:
        cols = {k: v for k, v in fields.items() if k in self._PAPER_COLS}
        cols["status"] = status
        cols["updated_at"] = now_iso()
        assigns = ", ".join(f"{k} = ?" for k in cols)
        self.conn.execute(f"UPDATE papers SET {assigns} WHERE id = ?",
                          list(cols.values()) + [paper_id])

    # -- facts -------------------------------------------------------------
    def insert_fact(self, paper_id: int, text: str, *, quote: str | None = None,
                    section: str | None = None, quant=None, confidence: float | None = None,
                    extractor_model: str | None = None, extractor_version: str | None = None,
                    status: str = "raw", created_run_id: str | None = None) -> int | None:
        """Insert a fact; returns its id, or None if an identical (paper, hash)
        fact already exists (idempotent re-extraction / within-paper dedup)."""
        h = content_hash(text)
        quant_json = json.dumps(quant) if isinstance(quant, (dict, list)) else quant
        cur = self.conn.execute(
            "INSERT OR IGNORE INTO facts (paper_id, text, quote, section, quant, "
            "confidence, extractor_model, extractor_version, content_hash, status, "
            "created_run_id) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (paper_id, text, quote, section, quant_json, confidence,
             extractor_model, extractor_version, h, status, created_run_id))
        if cur.rowcount == 0:
            return None
        return int(cur.lastrowid)

    def set_fact_claim(self, fact_id: int, claim_id: int, status: str = "reduced") -> None:
        self.conn.execute("UPDATE facts SET claim_id = ?, status = ? WHERE id = ?",
                          (claim_id, status, fact_id))

    def set_fact_status(self, fact_id: int, status: str) -> None:
        self.conn.execute("UPDATE facts SET status = ? WHERE id = ?", (status, fact_id))

    def get_fact(self, fact_id: int) -> sqlite3.Row | None:
        return self.conn.execute("SELECT * FROM facts WHERE id = ?", (fact_id,)).fetchone()

    def facts_for_paper(self, paper_id: int) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM facts WHERE paper_id = ? ORDER BY id", (paper_id,)).fetchall()

    def facts_by_status(self, *statuses: str) -> list[sqlite3.Row]:
        marks = ", ".join("?" for _ in statuses)
        return self.conn.execute(
            f"SELECT * FROM facts WHERE status IN ({marks}) ORDER BY id", statuses).fetchall()

    def search_facts(self, query: str, limit: int = 20) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT f.* FROM facts_fts x JOIN facts f ON f.id = x.rowid "
            "WHERE facts_fts MATCH ? ORDER BY rank LIMIT ?", (query, limit)).fetchall()

    # -- entities ----------------------------------------------------------
    def add_fact_entity(self, fact_id: int, entity_type: str, entity_id: str,
                        entity_label: str | None = None) -> None:
        self.conn.execute(
            "INSERT OR IGNORE INTO fact_entities (fact_id, entity_type, entity_id, "
            "entity_label) VALUES (?,?,?,?)", (fact_id, entity_type, entity_id, entity_label))

    def entities_for_fact(self, fact_id: int) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM fact_entities WHERE fact_id = ?", (fact_id,)).fetchall()

    def facts_with_entity(self, entity_type: str, entity_id: str) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT f.* FROM facts f JOIN fact_entities e ON e.fact_id = f.id "
            "WHERE e.entity_type = ? AND e.entity_id = ? ORDER BY f.id",
            (entity_type, entity_id)).fetchall()

    # -- claims / assertions ----------------------------------------------
    def insert_claim(self, canonical_text: str, *, created_run_id: str | None = None) -> int:
        cur = self.conn.execute(
            "INSERT INTO claims (canonical_text, created_run_id, updated_at) VALUES (?,?,?)",
            (canonical_text, created_run_id, now_iso()))
        return int(cur.lastrowid)

    def get_claim(self, claim_id: int) -> sqlite3.Row | None:
        return self.conn.execute("SELECT * FROM claims WHERE id = ?", (claim_id,)).fetchone()

    def set_claim_origin(self, claim_id: int, origin_paper_id: int | None,
                         provenance_status: str) -> None:
        self.conn.execute(
            "UPDATE claims SET origin_paper_id = ?, provenance_status = ?, updated_at = ? "
            "WHERE id = ?", (origin_paper_id, provenance_status, now_iso(), claim_id))

    def set_claim_assertion_count(self, claim_id: int, n: int) -> None:
        self.conn.execute("UPDATE claims SET n_assertions = ? WHERE id = ?", (n, claim_id))

    def claims_by_provenance(self, *statuses: str) -> list[sqlite3.Row]:
        marks = ", ".join("?" for _ in statuses)
        return self.conn.execute(
            f"SELECT * FROM claims WHERE provenance_status IN ({marks}) "
            f"ORDER BY n_assertions DESC, id", statuses).fetchall()

    def add_assertion(self, claim_id: int, paper_id: int, fact_id: int,
                      role: str = "asserts") -> None:
        self.conn.execute(
            "INSERT OR IGNORE INTO assertions (claim_id, paper_id, fact_id, role) "
            "VALUES (?,?,?,?)", (claim_id, paper_id, fact_id, role))

    def claim_asserters(self, claim_id: int) -> list[sqlite3.Row]:
        """Papers asserting a claim, each with its fact_id + role, ordered by
        publication year ascending (NULL years last)."""
        return self.conn.execute(
            "SELECT p.*, a.fact_id AS fact_id, a.role AS role "
            "FROM assertions a JOIN papers p ON p.id = a.paper_id "
            "WHERE a.claim_id = ? ORDER BY (p.year IS NULL), p.year ASC, p.id",
            (claim_id,)).fetchall()

    # -- citations ---------------------------------------------------------
    def add_citation(self, citing_id: int, cited_id: int, source: str = "openalex") -> None:
        self.conn.execute(
            "INSERT OR IGNORE INTO citations (citing_id, cited_id, source) VALUES (?,?,?)",
            (citing_id, cited_id, source))

    def references_of(self, paper_id: int) -> list[int]:
        return [r["cited_id"] for r in self.conn.execute(
            "SELECT cited_id FROM citations WHERE citing_id = ?", (paper_id,)).fetchall()]

    def citation_chain(self, paper_id: int, max_depth: int = 32) -> list[sqlite3.Row]:
        """All papers reachable by following citing->cited edges from paper_id
        (the backward citation cone), with depth. Recursive CTE."""
        return self.conn.execute(
            "WITH RECURSIVE chain(paper_id, depth) AS ("
            "  SELECT ?, 0"
            "  UNION"
            "  SELECT c.cited_id, chain.depth + 1 FROM citations c "
            "    JOIN chain ON c.citing_id = chain.paper_id WHERE chain.depth < ?"
            ") SELECT paper_id, MIN(depth) AS depth FROM chain GROUP BY paper_id",
            (paper_id, max_depth)).fetchall()

    # -- provenance log ----------------------------------------------------
    def log_provenance(self, claim_id: int, run_id: str | None, hop: int | None,
                       candidate_paper_id: int | None, decision: str,
                       notes: str | None = None) -> None:
        self.conn.execute(
            "INSERT INTO provenance_log (claim_id, run_id, hop, candidate_paper_id, "
            "decision, notes) VALUES (?,?,?,?,?,?)",
            (claim_id, run_id, hop, candidate_paper_id, decision, notes))

    def provenance_log_for(self, claim_id: int) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM provenance_log WHERE claim_id = ? ORDER BY id", (claim_id,)).fetchall()

    # -- run ledger --------------------------------------------------------
    def start_run(self, run_id: str, kind: str) -> None:
        self.conn.execute(
            "INSERT OR IGNORE INTO runs (run_id, kind, status, started_at) "
            "VALUES (?,?,'running',?)", (run_id, kind, now_iso()))

    def finish_run(self, run_id: str, status: str, stats: dict | None = None) -> None:
        self.conn.execute(
            "UPDATE runs SET status = ?, finished_at = ?, stats = ? WHERE run_id = ?",
            (status, now_iso(), json.dumps(stats or {}), run_id))

    def get_run(self, run_id: str) -> sqlite3.Row | None:
        return self.conn.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()
