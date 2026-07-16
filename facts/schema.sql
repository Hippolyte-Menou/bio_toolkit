-- literature-facts canonical schema. Idempotent (IF NOT EXISTS). Applied by
-- FactStore.init_schema(). SQLite; FTS5 required (bundled with CPython).

PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

-- Nodes: your library + fetched-for-provenance origins ------------------------
CREATE TABLE IF NOT EXISTS papers (
  id            INTEGER PRIMARY KEY,
  citekey       TEXT UNIQUE,                          -- MD/PDF filename stem, e.g. 2005_bredrup
  openalex_id   TEXT UNIQUE,                          -- nullable until resolved (Stage A′)
  pmid          TEXT,
  doi           TEXT,
  title         TEXT,
  year          INTEGER,
  journal       TEXT,
  md_path       TEXT,                                 -- relative to literature/mds/ (nullable)
  in_corpus     INTEGER NOT NULL DEFAULT 1,           -- 1 = full text held locally
  source        TEXT NOT NULL DEFAULT 'library',      -- 'library' | 'fetched-origin'
  status        TEXT NOT NULL DEFAULT 'pending',      -- pending|converted|extracted|failed|metadata-only
  added_run_id  TEXT,
  updated_at    TEXT
);

-- Canonical deduped claims (defined before facts, which forward-references it) -
CREATE TABLE IF NOT EXISTS claims (
  id                INTEGER PRIMARY KEY,
  canonical_text    TEXT NOT NULL,
  origin_paper_id   INTEGER REFERENCES papers(id),     -- resolved original mention
  provenance_status TEXT NOT NULL DEFAULT 'unresolved',
     -- unresolved|origin-in-corpus|origin-fetched|origin-outside-corpus|ambiguous|exhausted-budget
  n_assertions      INTEGER NOT NULL DEFAULT 0,
  created_run_id    TEXT,
  updated_at        TEXT
);

-- Raw atomic facts, one row per (paper, assertion) ---------------------------
CREATE TABLE IF NOT EXISTS facts (
  id              INTEGER PRIMARY KEY,
  paper_id        INTEGER NOT NULL REFERENCES papers(id),
  text            TEXT NOT NULL,                       -- self-contained statement (English)
  quote           TEXT,                                -- verbatim span from the MD (grounding)
  section         TEXT,                                -- gene|pathology|concept|method|anatomy|embryology|physiology|clinical-exam
  quant           TEXT,                                -- JSON: values, p-values, n, effect sizes (nullable)
  confidence      REAL,
  extractor_model TEXT,
  extractor_version TEXT,
  content_hash    TEXT NOT NULL,                       -- idempotent re-extraction / within-paper dedup
  claim_id        INTEGER REFERENCES claims(id),       -- set during reduction
  status          TEXT NOT NULL DEFAULT 'raw',         -- raw|reduced|cited-from|origin|discarded|review
  review_status   TEXT NOT NULL DEFAULT 'unreviewed',  -- unreviewed|approved|rejected
  created_run_id  TEXT,
  UNIQUE (paper_id, content_hash)
);
CREATE INDEX IF NOT EXISTS ix_facts_claim  ON facts(claim_id);
CREATE INDEX IF NOT EXISTS ix_facts_status ON facts(status);
CREATE INDEX IF NOT EXISTS ix_facts_paper  ON facts(paper_id);

-- Normalized entity links (keeps the store neutral & queryable) --------------
CREATE TABLE IF NOT EXISTS fact_entities (
  fact_id      INTEGER NOT NULL REFERENCES facts(id),
  entity_type  TEXT NOT NULL,                          -- gene|disease|method|anatomy|...
  entity_id    TEXT NOT NULL,                          -- HGNC symbol / disease-taxonomy id / controlled-vocab slug
  entity_label TEXT,
  PRIMARY KEY (fact_id, entity_type, entity_id)
);
CREATE INDEX IF NOT EXISTS ix_fe_entity ON fact_entities(entity_type, entity_id);

-- Edge: paper asserts claim (evidence = the specific fact) --------------------
CREATE TABLE IF NOT EXISTS assertions (
  claim_id INTEGER NOT NULL REFERENCES claims(id),
  paper_id INTEGER NOT NULL REFERENCES papers(id),
  fact_id  INTEGER NOT NULL REFERENCES facts(id),
  role     TEXT NOT NULL DEFAULT 'asserts',            -- asserts | originates
  PRIMARY KEY (claim_id, paper_id)
);
CREATE INDEX IF NOT EXISTS ix_assertions_paper ON assertions(paper_id);

-- Edge: citing -> cited (OpenAlex referenced_works / get_citations) -----------
CREATE TABLE IF NOT EXISTS citations (
  citing_id INTEGER NOT NULL REFERENCES papers(id),
  cited_id  INTEGER NOT NULL REFERENCES papers(id),
  source    TEXT NOT NULL DEFAULT 'openalex',
  PRIMARY KEY (citing_id, cited_id)
);
CREATE INDEX IF NOT EXISTS ix_citations_cited ON citations(cited_id);

-- Audit of the chase (why we concluded an origin) ----------------------------
CREATE TABLE IF NOT EXISTS provenance_log (
  id                 INTEGER PRIMARY KEY,
  claim_id           INTEGER NOT NULL REFERENCES claims(id),
  run_id             TEXT,
  hop                INTEGER,
  candidate_paper_id INTEGER REFERENCES papers(id),
  decision           TEXT,                             -- asserts-claim|no-assertion|no-fulltext|budget-stop|origin
  notes              TEXT
);
CREATE INDEX IF NOT EXISTS ix_provlog_claim ON provenance_log(claim_id);

-- Run ledger — idempotent / resumable ----------------------------------------
CREATE TABLE IF NOT EXISTS runs (
  run_id     TEXT PRIMARY KEY,                         -- e.g. backfill-20260716-w3 | incr-20260716
  kind       TEXT NOT NULL,                            -- convert|resolve|extract|reduce|chase
  status     TEXT NOT NULL,                            -- running|ok|degraded|failed
  stats      TEXT,                                     -- JSON
  started_at TEXT,
  finished_at TEXT
);

-- Full-text mirrors for dedup pre-filtering + search -------------------------
CREATE VIRTUAL TABLE IF NOT EXISTS facts_fts  USING fts5(text,           content='facts',  content_rowid='id');
CREATE VIRTUAL TABLE IF NOT EXISTS claims_fts USING fts5(canonical_text, content='claims', content_rowid='id');

-- Keep the FTS mirrors in sync with their content tables.
CREATE TRIGGER IF NOT EXISTS facts_ai AFTER INSERT ON facts BEGIN
  INSERT INTO facts_fts(rowid, text) VALUES (new.id, new.text);
END;
CREATE TRIGGER IF NOT EXISTS facts_ad AFTER DELETE ON facts BEGIN
  INSERT INTO facts_fts(facts_fts, rowid, text) VALUES ('delete', old.id, old.text);
END;
CREATE TRIGGER IF NOT EXISTS facts_au AFTER UPDATE ON facts BEGIN
  INSERT INTO facts_fts(facts_fts, rowid, text) VALUES ('delete', old.id, old.text);
  INSERT INTO facts_fts(rowid, text) VALUES (new.id, new.text);
END;

CREATE TRIGGER IF NOT EXISTS claims_ai AFTER INSERT ON claims BEGIN
  INSERT INTO claims_fts(rowid, canonical_text) VALUES (new.id, new.canonical_text);
END;
CREATE TRIGGER IF NOT EXISTS claims_ad AFTER DELETE ON claims BEGIN
  INSERT INTO claims_fts(claims_fts, rowid, canonical_text) VALUES ('delete', old.id, old.canonical_text);
END;
CREATE TRIGGER IF NOT EXISTS claims_au AFTER UPDATE ON claims BEGIN
  INSERT INTO claims_fts(claims_fts, rowid, canonical_text) VALUES ('delete', old.id, old.canonical_text);
  INSERT INTO claims_fts(rowid, canonical_text) VALUES (new.id, new.canonical_text);
END;
