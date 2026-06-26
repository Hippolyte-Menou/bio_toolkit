"""Unit tests for bio_toolkit.refdata.go_cellular_component. No network."""

from bio_toolkit.refdata import go_cellular_component as gcc


def test_manual_mappings_known_terms():
    m = gcc.MANUAL_MAPPINGS
    assert m["rod outer segment"] == "03 - Tunique interne/03 - Bâtonnets"
    assert m["cornea"] == "Cornée"
    assert m["mitochondrion"] == "Mitochondrie"
    # all keys are lowercase (lookup contract)
    assert all(k == k.lower() for k in m)


def test_categorize_component():
    assert gcc.categorize_component("photoreceptor outer segment") == "Photoreceptor structures"
    assert gcc.categorize_component("müller cell") == "Retinal cells"
    assert gcc.categorize_component("outer plexiform layer") == "Retinal layers"
    assert gcc.categorize_component("corneal stroma") == "Anterior segment"
    assert gcc.categorize_component("choroid") == "Uvea and choroid"
    assert gcc.categorize_component("optic nerve") == "Other ocular"
    assert gcc.categorize_component("mitochondrion") == "Generic subcellular"


def test_build_mapping_no_scan():
    out = gcc.build_mapping(scan_anatomy=False)
    assert out["metadata"]["anatomy_notes"] == 0
    assert out["metadata"]["confident_mappings"] == len(gcc.MANUAL_MAPPINGS)
    assert out["mappings"] == gcc.MANUAL_MAPPINGS
    # returned dict is a copy, not the module constant
    out["mappings"]["cornea"] = "MUTATED"
    assert gcc.MANUAL_MAPPINGS["cornea"] == "Cornée"


def test_load_anatomy_notes_scans_vault(tmp_path):
    anatomy = tmp_path / "B - Anatomie"
    (anatomy / "03 - Tunique interne").mkdir(parents=True)
    (anatomy / "03 - Tunique interne" / "01 - Photorécepteurs.md").write_text("x", encoding="utf-8")
    (anatomy / "Cornée.md").write_text("x", encoding="utf-8")
    # these should be skipped
    (anatomy / "Something - MOC.md").write_text("x", encoding="utf-8")
    (anatomy / "Atlas - Book.md").write_text("x", encoding="utf-8")

    moc = anatomy / "Anatomie de l'œil humain - MOC.md"
    moc.write_text("See [[Iris note]] and [[Other - MOC]]\n", encoding="utf-8")

    notes = gcc.load_anatomy_notes(anatomy, moc)
    assert "01 - Photorécepteurs" in notes
    assert "Cornée" in notes
    assert "Iris note" in notes           # from MOC wikilink
    assert "Something - MOC" not in notes  # skipped
    assert "Atlas - Book" not in notes     # skipped
    assert "Other - MOC" not in notes      # MOC wikilink skipped


def test_load_anatomy_notes_missing_root(tmp_path):
    # non-existent root + no MOC -> empty list, no error
    assert gcc.load_anatomy_notes(tmp_path / "nope", tmp_path / "nope.md") == []


def test_build_mapping_with_scan(tmp_path):
    anatomy = tmp_path / "B - Anatomie"
    anatomy.mkdir(parents=True)
    (anatomy / "Cornée.md").write_text("x", encoding="utf-8")
    # MOC kept OUTSIDE anatomy_root so the rglob scan doesn't also count it as a note
    moc = tmp_path / "moc.md"
    moc.write_text("[[Iris note]]\n", encoding="utf-8")

    out = gcc.build_mapping(anatomy_root=anatomy, anatomy_moc_path=moc, scan_anatomy=True)
    assert out["metadata"]["anatomy_notes"] == 2  # Cornée + Iris note
    assert out["metadata"]["confident_mappings"] == len(gcc.MANUAL_MAPPINGS)


def test_grouped_for_review_covers_all_mappings():
    grouped = gcc.grouped_for_review()
    total = sum(len(items) for items in grouped.values())
    assert total == len(gcc.MANUAL_MAPPINGS)
    # photoreceptor terms land in the right bucket
    photo = dict(grouped["Photoreceptor structures"])
    assert "rod outer segment" in photo
