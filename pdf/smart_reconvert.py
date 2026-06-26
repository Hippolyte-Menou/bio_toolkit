"""
smart_reconvert.py -- Find articles with low-quality PDF conversions and reconvert with Marker.

Scans article notes in a references directory for:
1. Articles with source-quality: low or abstract-only
2. Articles with converter: opendataloader (worth upgrading to Marker)
3. Scratch files flagged with reconvert: true
4. (--reassess) Articles with blank/unset source-quality: assess heuristically and update

Reconverts matching PDFs with Marker (ML engine) via ``bio_toolkit.pdf.pdf_to_md``,
then optionally resets extraction-status to pending.

Ported from the vault's top-level ``smart_reconvert.py``. The original pulled the
references / pdfs / pdfs-md / scratch directories from ``common.path_utils``; here
those directories are bundled into a :class:`ReconvertPaths` value passed to each
function so the scanner is reusable outside the vault layout. YAML frontmatter
parsing now reuses ``bio_toolkit.util.yaml``.

Usage:
    python -m bio_toolkit.pdf.smart_reconvert --vault-root ROOT ...
    python -m bio_toolkit.pdf.smart_reconvert --vault-root ROOT --reconvert
    python -m bio_toolkit.pdf.smart_reconvert --vault-root ROOT --reconvert --reset
    python -m bio_toolkit.pdf.smart_reconvert --vault-root ROOT --from-scratch
    python -m bio_toolkit.pdf.smart_reconvert --vault-root ROOT --reassess
    python -m bio_toolkit.pdf.smart_reconvert --vault-root ROOT --reassess --dry-run
"""

import argparse
import glob
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from bio_toolkit.util.yaml import (
    parse_frontmatter_from_file,
    replace_yaml_field_simple,
)


@dataclass
class ReconvertPaths:
    """Directories the reconvert scanner operates over.

    Replaces the hard-coded ``common.path_utils`` constants from the original
    vault script. Build from a vault root with :meth:`from_vault_root`, or
    construct directly when the layout differs.
    """

    vault_root: Path
    references_dir: Path
    pdfs_dir: Path
    pdfs_md_dir: Path
    scratch_dir: Path

    @classmethod
    def from_vault_root(cls, vault_root) -> "ReconvertPaths":
        """Build the standard vault layout under *vault_root*.

        Mirrors the original ``common.path_utils`` mapping:
        references = ``R - References/Automatically Imported``,
        pdfs = ``_assets/pdfs``, pdfs-md = ``_assets/pdfs-md``,
        scratch = ``_assets/code/data``.
        """
        root = Path(vault_root).resolve()
        return cls(
            vault_root=root,
            references_dir=root / "R - References" / "Automatically Imported",
            pdfs_dir=root / "_assets" / "pdfs",
            pdfs_md_dir=root / "_assets" / "pdfs-md",
            scratch_dir=root / "_assets" / "code" / "data",
        )


def parse_yaml_frontmatter(filepath: Path) -> dict:
    """Extract YAML frontmatter fields from a markdown file."""
    return parse_frontmatter_from_file(str(filepath))


def find_pdf_for_citekey(
    citekey: str, paths: ReconvertPaths, markdown_link: str = ""
) -> Path | None:
    """Find the source PDF for a given citekey."""
    if not citekey:
        return None

    # Try direct match in articles/
    for ext in [".pdf", ".PDF"]:
        direct = paths.pdfs_dir / "articles" / f"{citekey}{ext}"
        if direct.is_file():
            return direct

    # Try finding by pattern in all pdfs subdirs
    if paths.pdfs_dir.is_dir():
        for pdf_path in paths.pdfs_dir.rglob(f"*{citekey}*"):
            if pdf_path.suffix.lower() == ".pdf":
                return pdf_path

    # Try to resolve from the Markdown link in YAML
    if markdown_link:
        # Strip wikilink brackets
        md_path = markdown_link.strip("[]").strip("|").split("|")[0]
        md_path = md_path.replace("[[", "").replace("]]", "")
        # Convert pdfs-md path to pdfs path
        if "pdfs-md" in md_path:
            pdf_relative = md_path.replace("pdfs-md", "pdfs").replace(".md", ".pdf")
            resolved = (paths.vault_root / pdf_relative).resolve()
            if resolved.is_file():
                return resolved

    return None


def find_reconvert_candidates_from_notes(paths: ReconvertPaths) -> list[dict]:
    """Scan article notes for low-quality opendataloader conversions."""
    candidates = []

    if not paths.references_dir.is_dir():
        print(f"Directory not found: {paths.references_dir}")
        return candidates

    for note_path in sorted(paths.references_dir.glob("*.md")):
        yaml = parse_yaml_frontmatter(note_path)

        source_quality = yaml.get("source-quality", "")
        converter = yaml.get("converter", "")
        extraction_status = yaml.get("extraction-status", "")
        citekey = yaml.get("citekey", "")
        markdown_link = yaml.get("Markdown", "")

        # Candidate if: low/abstract-only quality AND opendataloader converter
        is_candidate = (
            source_quality in ("low", "abstract-only")
            and converter == "opendataloader"
        )

        if not is_candidate:
            continue

        # Try to find the source PDF
        pdf_path = find_pdf_for_citekey(citekey, paths, markdown_link)

        candidates.append(
            {
                "note": note_path.name,
                "note_path": str(note_path),
                "citekey": citekey,
                "source_quality": source_quality,
                "converter": converter,
                "extraction_status": extraction_status,
                "pdf_path": str(pdf_path) if pdf_path else None,
                "source": "note-scan",
            }
        )

    return candidates


def find_reconvert_candidates_from_scratch(paths: ReconvertPaths) -> list[dict]:
    """Check scratch files for reconvert: true flags."""
    candidates = []
    pattern = str(paths.scratch_dir / "scratch_extract_*.json")

    for scratch_path in sorted(glob.glob(pattern)):
        try:
            with open(scratch_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError):
            continue

        if not data.get("reconvert"):
            continue

        citekey = data.get("citekey", "")
        pdf_path = find_pdf_for_citekey(citekey, paths)

        candidates.append(
            {
                "note": data.get("article_name", "?"),
                "note_path": str(paths.vault_root / data.get("article_path", "")),
                "citekey": citekey,
                "source_quality": data.get("yaml_updates", {}).get("source-quality", "?"),
                "converter": "opendataloader",
                "extraction_status": data.get("yaml_updates", {}).get("extraction-status", "?"),
                "pdf_path": str(pdf_path) if pdf_path else None,
                "source": "scratch-file",
            }
        )

    return candidates


def assess_markdown_quality(md_path: Path | None) -> str:
    """Heuristically assess quality of a converted markdown file.

    Returns one of: 'high', 'medium', 'low', 'abstract-only'
    Conservative: only flags clear failures as low/abstract-only.
    Ambiguous cases return 'medium' (no reconversion triggered).
    """
    if md_path is None or not md_path.is_file():
        return "abstract-only"

    try:
        text = md_path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return "abstract-only"

    # Strip YAML frontmatter
    body = text
    if text.startswith("---"):
        end = text.find("\n---", 4)
        if end != -1:
            body = text[end + 4:]

    # Count substantive text (non-whitespace, non-image-ref lines)
    lines = body.splitlines()
    image_lines = sum(1 for l in lines if l.strip().startswith("![[") or l.strip().startswith("!["))
    text_chars = sum(len(l.strip()) for l in lines if not l.strip().startswith("!"))

    total_lines = len([l for l in lines if l.strip()])
    image_ratio = image_lines / total_lines if total_lines > 0 else 0

    if text_chars < 200:
        return "abstract-only"
    if image_ratio > 0.5 and text_chars < 1000:
        return "low"
    if text_chars < 500:
        return "low"

    # Check for garbled text indicators (high density of non-ASCII or replacement chars)
    non_ascii = sum(1 for c in body if ord(c) > 127 and c not in "àâäéèêëîïôöùûüçÀÂÄÉÈÊËÎÏÔÖÙÛÜÇ")
    if len(body) > 0 and non_ascii / len(body) > 0.15:
        return "low"

    return "medium"


def find_unassessed_articles(paths: ReconvertPaths) -> list[dict]:
    """Find articles with blank/unset source-quality and opendataloader converter."""
    unassessed = []

    if not paths.references_dir.is_dir():
        return unassessed

    blank_values = {"", "---", "none", "null"}

    for note_path in sorted(paths.references_dir.glob("*.md")):
        yaml = parse_yaml_frontmatter(note_path)

        converter = yaml.get("converter", "")
        if converter != "opendataloader":
            continue

        source_quality = yaml.get("source-quality", "").lower()
        if source_quality not in blank_values:
            continue

        citekey = yaml.get("citekey", "")
        markdown_link = yaml.get("Markdown", "")

        # Resolve converted markdown path
        md_path = None
        if citekey:
            candidate = paths.pdfs_md_dir / "articles" / f"{citekey}.md"
            if candidate.is_file():
                md_path = candidate
        if md_path is None and markdown_link:
            rel = markdown_link.strip("[]").replace("[[", "").replace("]]", "").split("|")[0]
            resolved = (paths.references_dir / rel).resolve()
            if resolved.is_file():
                md_path = resolved

        pdf_path = find_pdf_for_citekey(citekey, paths, markdown_link)

        unassessed.append(
            {
                "note": note_path.name,
                "note_path": str(note_path),
                "citekey": citekey,
                "md_path": str(md_path) if md_path else None,
                "pdf_path": str(pdf_path) if pdf_path else None,
            }
        )

    return unassessed


def write_source_quality(note_path: str, quality: str) -> bool:
    """Write source-quality value into article note YAML frontmatter."""
    try:
        with open(note_path, "r", encoding="utf-8") as f:
            content = f.read()

        new_content, replaced = replace_yaml_field_simple(content, "source-quality", quality)
        if not replaced:
            return False

        with open(note_path, "w", encoding="utf-8") as f:
            f.write(new_content)
        return True
    except (OSError, UnicodeDecodeError):
        return False


def reconvert_pdf(
    pdf_path: str,
    pdfs_dir: Path,
    output_dir: Path,
    force: bool = True,
    python_exe: str | None = None,
) -> bool:
    """Reconvert a PDF using Marker via ``bio_toolkit.pdf.pdf_to_md``.

    Runs the ported converter as a subprocess (``-m bio_toolkit.pdf.pdf_to_md``)
    so the heavyweight Marker import stays out of this process.
    """
    python = python_exe or sys.executable
    cmd = [
        python,
        "-m",
        "bio_toolkit.pdf.pdf_to_md",
        pdf_path,
        "--pdfs-dir",
        str(pdfs_dir),
        "--output-dir",
        str(output_dir),
        "--marker",
    ]
    if force:
        cmd.append("--force")

    print(f'  Running: {" ".join(cmd)}')
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=1800,  # 30 min timeout per PDF (Marker model loading + CPU conversion)
        )
        if result.returncode == 0:
            print("  Conversion successful")
            return True
        else:
            print(f"  Conversion failed: {result.stderr.strip()}")
            return False
    except subprocess.TimeoutExpired:
        print("  Conversion timed out (>30 min)")
        return False
    except Exception as e:
        print(f"  Conversion error: {e}")
        return False


def reset_extraction_status(note_path: str) -> bool:
    """Reset extraction-status to pending in an article note."""
    try:
        with open(note_path, "r", encoding="utf-8") as f:
            content = f.read()

        new_content, replaced = replace_yaml_field_simple(
            content, "extraction-status", "pending"
        )
        if not replaced:
            return False

        # Also blank source-quality to indicate reconversion
        new_content = re.sub(
            r"^source-quality:.*$",
            "source-quality:",
            new_content,
            count=1,
            flags=re.MULTILINE,
        )

        # Update converter field
        new_content = re.sub(
            r"^converter:.*$",
            "converter: marker",
            new_content,
            count=1,
            flags=re.MULTILINE,
        )

        with open(note_path, "w", encoding="utf-8") as f:
            f.write(new_content)

        return True
    except (OSError, UnicodeDecodeError) as e:
        print(f"  Failed to reset {note_path}: {e}")
        return False


def main(argv: list[str] | None = None):
    parser = argparse.ArgumentParser(
        description="Find and reconvert low-quality PDF conversions with Marker"
    )
    parser.add_argument(
        "--vault-root",
        required=True,
        help="Vault root; references/pdfs/pdfs-md/scratch dirs are derived from it",
    )
    parser.add_argument(
        "--reconvert",
        action="store_true",
        help="Actually reconvert PDFs (default: scan and report only)",
    )
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Reset extraction-status to pending after reconversion (use with --reconvert)",
    )
    parser.add_argument(
        "--from-scratch",
        action="store_true",
        help="Also check scratch files for reconvert flags",
    )
    parser.add_argument(
        "--reassess",
        action="store_true",
        help="Assess articles with blank source-quality, update notes, surface low/abstract-only as reconversion candidates",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="With --reassess: preview quality assessments without writing to notes",
    )
    args = parser.parse_args(argv)

    paths = ReconvertPaths.from_vault_root(args.vault_root)

    # --reassess: assess blank source-quality articles and update notes
    if args.reassess:
        unassessed = find_unassessed_articles(paths)
        if not unassessed:
            print("No unassessed articles found (all have source-quality set).")
            return 0

        print(f"Found {len(unassessed)} article(s) with blank source-quality:\n")
        print(f'{"#":<4} {"Article":<35} {"Assessed Quality":<18} {"Action":<20}')
        print("-" * 80)

        reconvert_after = []
        for i, a in enumerate(unassessed, 1):
            md_path = Path(a["md_path"]) if a["md_path"] else None
            quality = assess_markdown_quality(md_path)
            action = "write quality"
            if quality in ("low", "abstract-only"):
                action = "reconvert candidate"
                if a["pdf_path"]:
                    reconvert_after.append(
                        {
                            "note": a["note"],
                            "note_path": a["note_path"],
                            "citekey": a["citekey"],
                            "source_quality": quality,
                            "converter": "opendataloader",
                            "extraction_status": "extracted",
                            "pdf_path": a["pdf_path"],
                            "source": "reassess",
                        }
                    )
                else:
                    action = "reconvert candidate (NO PDF)"

            print(f'{i:<4} {a["note"]:<35} {quality:<18} {action:<20}')

            if not args.dry_run:
                write_source_quality(a["note_path"], quality)

        if args.dry_run:
            print("\n(dry-run: no files modified)")
        else:
            print(f"\nUpdated source-quality for {len(unassessed)} article(s).")

        if reconvert_after:
            print(
                f"\n{len(reconvert_after)} article(s) assessed as low/abstract-only and queued as reconversion candidates."
            )
            if not args.reconvert:
                print("Run with --reconvert to reconvert them.")
            else:
                actionable = [c for c in reconvert_after if c["pdf_path"]]
                print(f"\nReconverting {len(actionable)} PDF(s) with Marker...\n")
                successes = 0
                for c in actionable:
                    print(f'[{successes + 1}/{len(actionable)}] {c["note"]}')
                    ok = reconvert_pdf(c["pdf_path"], paths.pdfs_dir, paths.pdfs_md_dir)
                    if ok:
                        successes += 1
                        if args.reset:
                            reset_ok = reset_extraction_status(c["note_path"])
                            if reset_ok:
                                print("  Reset extraction-status to pending")
                            else:
                                print("  Warning: could not reset extraction-status")
                print(f"\nDone: {successes}/{len(actionable)} reconverted successfully.")
                if args.reset and successes > 0:
                    print(f"\n{successes} article(s) reset to pending. Re-run extraction:")
                    print("  /extract-facts batch")
        else:
            print("No reconversion candidates from reassessment.")
        return 0

    # Collect candidates
    candidates = find_reconvert_candidates_from_notes(paths)
    if args.from_scratch:
        scratch_candidates = find_reconvert_candidates_from_scratch(paths)
        # Deduplicate by citekey
        existing_citekeys = {c["citekey"] for c in candidates}
        for sc in scratch_candidates:
            if sc["citekey"] not in existing_citekeys:
                candidates.append(sc)

    if not candidates:
        print("No reconversion candidates found.")
        return 0

    # Report
    print(f"Found {len(candidates)} reconversion candidate(s):\n")
    print(f'{"#":<4} {"Article":<35} {"Quality":<15} {"PDF Found":<12} {"Source":<15}')
    print("-" * 81)

    actionable = []
    for i, c in enumerate(candidates, 1):
        pdf_found = "YES" if c["pdf_path"] else "MISSING"
        print(f'{i:<4} {c["note"]:<35} {c["source_quality"]:<15} {pdf_found:<12} {c["source"]:<15}')
        if c["pdf_path"]:
            actionable.append(c)

    missing = len(candidates) - len(actionable)
    if missing:
        print(f"\n{missing} article(s) have no PDF -- cannot reconvert.")

    if not args.reconvert:
        # Print copy-pasteable commands
        if actionable:
            print("\nTo reconvert, run:")
            print("  python -m bio_toolkit.pdf.smart_reconvert --vault-root ROOT --reconvert")
            print("\nOr manually:")
            for c in actionable:
                print(
                    f'  python -m bio_toolkit.pdf.pdf_to_md --marker --force '
                    f'--pdfs-dir {paths.pdfs_dir} --output-dir {paths.pdfs_md_dir} {c["pdf_path"]}'
                )
        return 0

    # Execute reconversion
    print(f"\nReconverting {len(actionable)} PDF(s) with Marker...\n")
    successes = 0
    for c in actionable:
        print(f'[{successes + 1}/{len(actionable)}] {c["note"]}')
        ok = reconvert_pdf(c["pdf_path"], paths.pdfs_dir, paths.pdfs_md_dir)
        if ok:
            successes += 1
            if args.reset:
                reset_ok = reset_extraction_status(c["note_path"])
                if reset_ok:
                    print("  Reset extraction-status to pending")
                else:
                    print("  Warning: could not reset extraction-status")

    print(f"\nDone: {successes}/{len(actionable)} reconverted successfully.")

    if args.reset and successes > 0:
        print(f"\n{successes} article(s) reset to pending. Re-run extraction:")
        print("  /extract-facts batch")

    return 0 if successes == len(actionable) else 1


if __name__ == "__main__":
    sys.exit(main())
