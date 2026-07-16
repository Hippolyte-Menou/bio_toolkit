from bio_toolkit.facts import normalize


def test_gene_normalizes_via_hgnc(monkeypatch):
    monkeypatch.setattr(normalize, "_gene", lambda n: "DCN" if n.lower() == "decorin" else None)
    r = normalize.normalize_entity("gene", "decorin")
    assert r["entity_id"] == "DCN" and r["confident"]


def test_unknown_gene_flagged(monkeypatch):
    monkeypatch.setattr(normalize, "_gene", lambda n: None)
    r = normalize.normalize_entity("gene", "xyz")
    assert r["entity_id"] is None and not r["confident"]


def test_slug_fallback():
    r = normalize.normalize_entity("method", "Whole Exome Sequencing")
    assert r["entity_id"] == "whole-exome-sequencing" and r["entity_type"] == "method"


def test_disease_uses_taxonomy(monkeypatch):
    import bio_toolkit.refdata.disease_taxonomy as dt
    monkeypatch.setattr(normalize, "_disease_rules", lambda: [("rule",)])
    monkeypatch.setattr(dt, "guess_pathology_tag", lambda name, rules: ("retinitis-pigmentosa", True))
    r = normalize.normalize_entity("disease", "Retinitis pigmentosa 3")
    assert r["entity_id"] == "retinitis-pigmentosa" and r["confident"]
