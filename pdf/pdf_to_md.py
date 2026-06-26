"""
pdf_to_md.py -- Convert PDFs to markdown.

Default engine: opendataloader-pdf (fast, rule-based, ~0.05s/page).
Quality override: Marker (ML-based, better tables/layout, ~54s/page on CPU).

Converts scientific PDFs from an input directory into clean markdown files in
an output directory, preserving subfolder structure. Adds YAML frontmatter with
source metadata and an optional topic tag.

Ported from the vault's ``pdf-conversion/pdf_to_md.py``. The original hard-coded
the vault's ``_assets/pdfs`` / ``_assets/pdfs-md`` layout; here the input and
output roots are explicit arguments so the converter is reusable.

The heavyweight converters (OpenDataLoader, Marker) are imported lazily inside
the conversion functions so this module imports cleanly without them installed.

Usage:
    python -m bio_toolkit.pdf.pdf_to_md <path> --pdfs-dir IN --output-dir OUT
    python -m bio_toolkit.pdf.pdf_to_md <path> ... --marker          # high-quality (Marker ML)
    python -m bio_toolkit.pdf.pdf_to_md <path> ... --topic X         # tag output
    python -m bio_toolkit.pdf.pdf_to_md <path> ... --force           # reconvert existing
    python -m bio_toolkit.pdf.pdf_to_md <path> ... --disable-images  # skip image extraction
"""

import argparse
import json
import logging
import os
import shutil
import sys
import tempfile
from datetime import date
from pathlib import Path
from unicodedata import category as unicode_category

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


def _safe_copy(pdf_path: Path, tmp_dir: str) -> Path | None:
    """If the filename contains non-ASCII dash/hyphen lookalikes, copy the PDF
    to *tmp_dir* with a sanitized name and return the new path.
    Returns None when no sanitization is needed (caller uses original path).

    Handles: U+2010 HYPHEN, U+2011 NON-BREAKING HYPHEN, U+2012 FIGURE DASH,
    U+2013 EN DASH, U+2014 EM DASH, U+2015 HORIZONTAL BAR, U+FE58 SMALL EM DASH,
    U+FF0D FULLWIDTH HYPHEN-MINUS, and any other Unicode Pd (Dash_Punctuation).
    """
    name = pdf_path.name
    safe = "".join(
        "-" if (ch != "-" and unicode_category(ch) == "Pd") else ch
        for ch in name
    )
    if safe == name:
        return None
    logger.info("Sanitized filename: %s -> %s", name, safe)
    dest = Path(tmp_dir) / safe
    shutil.copy2(str(pdf_path), str(dest))
    return dest


def build_frontmatter(
    source_relative: str,
    converter: str,
    topic: str | None = None,
) -> str:
    """Build YAML frontmatter for a converted markdown file."""
    lines = [
        "---",
        f'source-pdf: "{source_relative}"',
        f"converted: {date.today().isoformat()}",
        f"converter: {converter}",
        "tags:",
        "  - source/pdf-conversion",
    ]
    if topic:
        lines.append(f"  - topic/{topic}")
    lines.append("---")
    return "\n".join(lines)


def compute_output_path(pdf_path: Path, pdfs_dir: Path, output_dir: Path) -> Path:
    """Compute the output .md path, mirroring subfolder structure from *pdfs_dir*."""
    try:
        relative = pdf_path.resolve().relative_to(pdfs_dir.resolve())
    except ValueError:
        # PDF is outside the input root -- output goes flat into output_dir
        relative = Path(pdf_path.stem)
    return output_dir / relative.with_suffix(".md")


def compute_source_relative(pdf_path: Path, pdfs_dir: Path) -> str:
    """Compute the source-pdf field value (relative to *pdfs_dir*)."""
    try:
        return str(pdf_path.resolve().relative_to(pdfs_dir.resolve())).replace("\\", "/")
    except ValueError:
        return pdf_path.name


# -- OpenDataLoader engine ----------------------------------------------------

def convert_single_opendataloader(
    pdf_path: Path,
    pdfs_dir: Path,
    output_dir: Path,
    topic: str | None = None,
    force: bool = False,
    disable_images: bool = False,
) -> dict:
    """Convert a single PDF using opendataloader-pdf (fast, rule-based).

    ``opendataloader_pdf`` is imported lazily so importing this module does not
    require the heavyweight converter.
    """
    from opendataloader_pdf import convert

    result = {"path": str(pdf_path), "success": False, "error": None, "skipped": False}
    output_path = compute_output_path(pdf_path, pdfs_dir, output_dir)

    if output_path.exists() and not force:
        logger.info("Skipped (already exists): %s", pdf_path.name)
        result["skipped"] = True
        return result

    tmp_dir = None
    try:
        logger.info("Converting (opendataloader): %s", pdf_path.name)
        tmp_dir = tempfile.mkdtemp(prefix="odl_")

        safe_path = _safe_copy(pdf_path, tmp_dir) or pdf_path

        # opendataloader default output is JSON containing markdown content.
        # We request markdown format and extract from the response.
        convert(
            input_path=str(safe_path),
            output_dir=tmp_dir,
            format="markdown",
            quiet=True,
        )

        # Find the generated output file (may be .md or .json depending on version)
        out_files = [f for f in Path(tmp_dir).iterdir() if f.is_file() and f.suffix in {".md", ".json"}]
        if not out_files:
            raise FileNotFoundError("opendataloader produced no output")

        raw = out_files[0].read_text(encoding="utf-8")

        # If the output is JSON-wrapped markdown, extract the content
        if raw.lstrip().startswith("{"):
            data = json.loads(raw)
            raw_text = data.get("formats", {}).get("markdown", {}).get("content", "")
            if not raw_text:
                # Try other format keys
                for fmt in data.get("formats", {}).values():
                    if isinstance(fmt, dict) and "content" in fmt:
                        raw_text = fmt["content"]
                        break
            if not raw_text:
                raise ValueError("Could not extract markdown content from JSON output")
        else:
            raw_text = raw

        # Build final content with frontmatter
        source_rel = compute_source_relative(pdf_path, pdfs_dir)
        frontmatter = build_frontmatter(source_rel, "opendataloader", topic)
        content = frontmatter + "\n\n" + raw_text

        # Write markdown
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(content, encoding="utf-8")

        # Copy images if any were extracted (opendataloader puts them in <stem>_images/)
        if not disable_images:
            tmp_images_dir = Path(tmp_dir) / (out_files[0].stem + "_images")
            if tmp_images_dir.is_dir():
                images_dir = output_path.parent / (output_path.stem + "_images")
                images_dir.mkdir(parents=True, exist_ok=True)
                for img in tmp_images_dir.iterdir():
                    if img.is_file():
                        shutil.copy2(str(img), str(images_dir / img.name))
                        logger.debug("Saved image: %s", img.name)

        logger.info("Converted: %s -> %s", pdf_path.name, output_path)
        result["success"] = True
        result["output"] = str(output_path)

    except Exception as e:
        logger.error("Failed to convert %s: %s", pdf_path.name, e)
        result["error"] = str(e)

    finally:
        if tmp_dir and os.path.exists(tmp_dir):
            shutil.rmtree(tmp_dir, ignore_errors=True)

    return result


# -- Marker engine ------------------------------------------------------------

def convert_single_marker(
    pdf_path: Path,
    pdfs_dir: Path,
    output_dir: Path,
    converter,
    text_from_rendered,
    topic: str | None = None,
    force: bool = False,
    disable_images: bool = False,
) -> dict:
    """Convert a single PDF using Marker (ML-based, high quality).

    The Marker *converter* and *text_from_rendered* callables are passed in by
    the caller (loaded lazily in ``main``), so this function has no hard import
    of the heavyweight ML package.
    """
    result = {"path": str(pdf_path), "success": False, "error": None, "skipped": False}
    output_path = compute_output_path(pdf_path, pdfs_dir, output_dir)

    if output_path.exists() and not force:
        logger.info("Skipped (already exists): %s", pdf_path.name)
        result["skipped"] = True
        return result

    marker_tmp = None
    try:
        logger.info("Converting (marker): %s", pdf_path.name)
        marker_tmp = tempfile.mkdtemp(prefix="marker_san_")
        safe_path = _safe_copy(pdf_path, marker_tmp) or pdf_path
        rendered = converter(str(safe_path))
        text, _, images = text_from_rendered(rendered)

        # Build final content with frontmatter
        source_rel = compute_source_relative(pdf_path, pdfs_dir)
        frontmatter = build_frontmatter(source_rel, "marker", topic)
        content = frontmatter + "\n\n" + text

        # Write markdown
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(content, encoding="utf-8")

        # Write images if any
        if images and not disable_images:
            images_dir = output_path.parent / (output_path.stem + "_images")
            images_dir.mkdir(parents=True, exist_ok=True)
            for img_name, img_data in images.items():
                img_path = images_dir / img_name
                img_data.save(str(img_path))
                logger.debug("Saved image: %s", img_path)

        logger.info("Converted: %s -> %s", pdf_path.name, output_path)
        result["success"] = True
        result["output"] = str(output_path)

    except Exception as e:
        logger.error("Failed to convert %s: %s", pdf_path.name, e)
        result["error"] = str(e)

    finally:
        if marker_tmp and os.path.exists(marker_tmp):
            shutil.rmtree(marker_tmp, ignore_errors=True)

    return result


# -- Shared logic -------------------------------------------------------------

def collect_pdfs(path: Path) -> list[Path]:
    """Collect PDF files from a path (single file or directory)."""
    if path.is_file() and path.suffix.lower() == ".pdf":
        return [path]
    elif path.is_dir():
        pdfs = sorted(path.rglob("*.pdf"))
        logger.info("Found %d PDF files in %s", len(pdfs), path)
        return pdfs
    else:
        logger.error("Not a valid PDF file or directory: %s", path)
        return []


def main(argv: list[str] | None = None):
    parser = argparse.ArgumentParser(
        description="Convert PDFs to markdown. Default: opendataloader (fast). Use --marker for ML quality.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("path", help="Path to a PDF file or folder of PDFs")
    parser.add_argument(
        "--pdfs-dir",
        required=True,
        help="Input root; output mirrors subfolder structure relative to this dir",
    )
    parser.add_argument(
        "--output-dir",
        required=True,
        help="Output root for converted .md files",
    )
    parser.add_argument(
        "--marker",
        action="store_true",
        help="Use Marker (ML-based) instead of opendataloader (rule-based)",
    )
    parser.add_argument(
        "--topic",
        default=None,
        help="Topic tag to add to output frontmatter (e.g. X-linked-RD)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Reconvert even if markdown already exists",
    )
    parser.add_argument(
        "--disable-images",
        action="store_true",
        help="Skip image extraction from PDFs",
    )
    args = parser.parse_args(argv)

    pdfs_dir = Path(args.pdfs_dir).resolve()
    output_dir = Path(args.output_dir).resolve()

    input_path = Path(args.path).resolve()
    pdfs = collect_pdfs(input_path)
    if not pdfs:
        logger.error("No PDFs found at: %s", input_path)
        sys.exit(1)

    use_marker = args.marker

    converter = None
    text_from_rendered = None
    if use_marker:
        # Lazy import -- only load heavy ML models when explicitly requested
        logger.info("Loading Marker models...")
        from marker.converters.pdf import PdfConverter
        from marker.models import create_model_dict
        from marker.output import text_from_rendered

        converter = PdfConverter(artifact_dict=create_model_dict())
        logger.info("Models loaded.")
    else:
        logger.info("Using opendataloader (fast mode)")

    # Convert
    results = []
    for pdf in pdfs:
        if use_marker:
            r = convert_single_marker(
                pdf,
                pdfs_dir,
                output_dir,
                converter,
                text_from_rendered,
                topic=args.topic,
                force=args.force,
                disable_images=args.disable_images,
            )
        else:
            r = convert_single_opendataloader(
                pdf,
                pdfs_dir,
                output_dir,
                topic=args.topic,
                force=args.force,
                disable_images=args.disable_images,
            )
        results.append(r)

    # Summary
    engine = "marker" if use_marker else "opendataloader"
    successes = [r for r in results if r["success"]]
    skipped = [r for r in results if r["skipped"]]
    failures = [r for r in results if not r["success"] and not r["skipped"]]

    print(f"\n--- Summary ({engine}) ---")
    print(f"Converted: {len(successes)}")
    print(f"Skipped:   {len(skipped)}")
    print(f"Failed:    {len(failures)}")

    if failures:
        print("\nFailed files:")
        for r in failures:
            print(f"  {r['path']}: {r['error']}")
        sys.exit(1)


if __name__ == "__main__":
    main()
