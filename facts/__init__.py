"""Canonical fact/claim store-access layer for the literature-facts pipeline.

`FactStore` is the ONE class that opens, initialises, reads, and writes the
SQLite fact/claim store (literature/facts/facts.sqlite). External consumers
(vault, co-scientist, interactive browsing) import the read helpers; the
project's deterministic scripts do their writes through the same class.
"""

from bio_toolkit.facts.store import FactStore, now_iso

__all__ = ["FactStore", "now_iso"]
