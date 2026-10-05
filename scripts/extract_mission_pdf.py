#!/usr/bin/env python3
"""
Extract mission content from a PDF and generate draft markdown files for review.

Handles both single-mission PDFs and multi-mission packs. One draft file is
written per detected mission, prefixed with _draft_, into source-pdfs/. Review
each draft, clean it up, then move it to the appropriate summaries/missions/
subdirectory (primary-objectives, secondary-objectives, deployment-zones, or twists).

Usage:
    python scripts/extract_mission_pdf.py <path-to-pdf> [options]

    --force        Re-extract even if this PDF has already been processed.
    --output-dir   Override the default draft output directory.
    --list         Print the current extraction manifest and exit.

Requirements:
    pip install -r scripts/requirements.txt
"""

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
MANIFEST_PATH = REPO_ROOT / "summaries" / "missions" / "source-pdfs" / "manifest.json"
DEFAULT_OUTPUT_DIR = MANIFEST_PATH.parent

# Structural section headings commonly found in GW mission layouts.
# Repeated occurrences across pages suggest a multi-mission pack.
_STRUCTURAL_MARKERS = (
    "Primary Objective",
    "Secondary Objective",
    "Victory Conditions",
    "Deployment",
    "Special Rules",
    "Setup",
)

# A line that looks like a mission title: short, starts with a capital,
# no trailing punctuation, and not a structural marker itself.
_TITLE_CANDIDATE_RE = re.compile(r"^[A-Z][A-Za-z ,'\-]{2,55}$")

# Explicit mission-number patterns often used in GW packs.
_MISSION_NUMBER_RE = re.compile(
    r"^(?:MISSION|Mission)\s+\d+|^\d+\.\s+[A-Z][A-Za-z ]{3,50}$",
    re.MULTILINE,
)


# ---------------------------------------------------------------------------
# Manifest helpers
# ---------------------------------------------------------------------------

def load_manifest() -> dict:
    if MANIFEST_PATH.exists():
        return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    return {"extractions": []}


def save_manifest(manifest: dict) -> None:
    MANIFEST_PATH.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def sha256_of_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def print_manifest(manifest: dict) -> None:
    extractions = manifest.get("extractions", [])
    if not extractions:
        print("No extractions recorded yet.")
        return

    for entry in extractions:
        print(f"\n{entry['filename']}")
        print(f"  Extracted : {entry['extracted_at']}")
        print(f"  SHA256    : {entry['sha256'][:16]}...")
        print(f"  Pages     : {entry.get('page_count', '?')}")
        print(f"  Missions  : {entry.get('missions_detected', '?')}")
        outputs = entry.get("outputs", [])
        for out in outputs:
            title = f"  [{out['detected_title']}]" if out.get("detected_title") else ""
            print(f"  {out['status']:12s}  {out['draft_file']}{title}")


# ---------------------------------------------------------------------------
# PDF extraction
# ---------------------------------------------------------------------------

def extract_pages(pdf_path: Path) -> list[str]:
    try:
        import pypdf
    except ImportError:
        print(
            "pypdf is required. Install it with:\n"
            "  pip install -r scripts/requirements.txt",
            file=sys.stderr,
        )
        sys.exit(1)

    reader = pypdf.PdfReader(str(pdf_path))
    return [page.extract_text() or "" for page in reader.pages]


# ---------------------------------------------------------------------------
# Mission boundary detection
# ---------------------------------------------------------------------------

def _structural_density(text: str) -> int:
    """Count how many structural markers appear in a block of text."""
    return sum(1 for m in _STRUCTURAL_MARKERS if m in text)


def _looks_like_mission_start(page_text: str) -> tuple[bool, str | None]:
    """
    Return (is_start, detected_title).

    A page looks like a mission start if:
    - Its first substantive line is title-like (short, capitalised, no punctuation),
    - AND the page contains at least one structural marker.
    """
    lines = [l.strip() for l in page_text.splitlines() if l.strip()]
    if not lines:
        return False, None

    first_line = lines[0]
    has_structure = _structural_density(page_text) >= 1
    is_title = (
        _TITLE_CANDIDATE_RE.match(first_line) is not None
        and first_line not in _STRUCTURAL_MARKERS
        and has_structure
    )

    return is_title, first_line if is_title else None


def detect_missions(pages: list[str]) -> list[tuple[str | None, str]]:
    """
    Return a list of (detected_title_or_None, combined_page_text) tuples.

    Single-mission PDFs return one element. Multi-mission packs return one
    element per detected mission. Falls back to a single-mission result if
    boundary detection produces no useful splits.
    """
    full_text = "\n\n".join(pages)

    # Count repetitions of structural markers to decide if this is a pack.
    max_repetitions = max(
        (full_text.count(m) for m in _STRUCTURAL_MARKERS),
        default=0,
    )

    if max_repetitions <= 1:
        # Structural elements appear at most once — almost certainly one mission.
        return [(None, full_text)]

    # Attempt page-level boundary detection.
    missions: list[tuple[str | None, str]] = []
    current_pages: list[str] = []
    current_title: str | None = None

    for page_text in pages:
        is_start, title = _looks_like_mission_start(page_text)

        if is_start and current_pages:
            missions.append((current_title, "\n\n".join(current_pages)))
            current_pages = [page_text]
            current_title = title
        else:
            current_pages.append(page_text)
            if current_title is None and title:
                current_title = title

    if current_pages:
        missions.append((current_title, "\n\n".join(current_pages)))

    # Safety fallback: if detection collapsed everything into one block, return it.
    if len(missions) <= 1:
        return [(current_title if missions else None, full_text)]

    return missions


# ---------------------------------------------------------------------------
# Draft generation
# ---------------------------------------------------------------------------

def _to_slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def generate_draft(
    pdf_filename: str,
    mission_index: int,
    total_missions: int,
    detected_title: str | None,
    body_text: str,
) -> str:
    slug_hint = (
        _to_slug(detected_title)
        if detected_title
        else _to_slug(Path(pdf_filename).stem)
    )
    display_title = detected_title or Path(pdf_filename).stem.replace("-", " ").replace("_", " ").title()
    pack_note = f" (mission {mission_index} of {total_missions})" if total_missions > 1 else ""

    return (
        f"---\n"
        f"name: {slug_hint}  # TODO: confirm slug\n"
        f"source_editions: []  # TODO: add e.g. [heresy3]\n"
        f"titan_mission: false\n"
        f"points_minimum: null\n"
        f"faction_tags: []\n"
        f"---\n\n"
        f"# {display_title}\n\n"
        f"<!-- SOURCE: {pdf_filename}{pack_note} -->\n"
        f"<!-- STATUS: needs-review -->\n"
        f"<!-- TODO: Review, restructure, and move to the appropriate\n"
        f"     summaries/missions/ subdirectory when ready. -->\n\n"
        f"{body_text.strip()}\n"
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Extract mission content from a PDF into draft markdown files.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("pdf", nargs="?", help="Path to the PDF file to extract.")
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-extract even if this PDF has already been processed.",
    )
    parser.add_argument(
        "--output-dir",
        default=str(DEFAULT_OUTPUT_DIR),
        help="Directory to write draft markdown files into (default: summaries/missions/source-pdfs/).",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="Print the current extraction manifest and exit.",
    )
    args = parser.parse_args()

    if args.list:
        print_manifest(load_manifest())
        return

    if not args.pdf:
        parser.print_help()
        sys.exit(1)

    pdf_path = Path(args.pdf).resolve()

    if not pdf_path.exists():
        print(f"Error: File not found: {pdf_path}", file=sys.stderr)
        sys.exit(1)

    if pdf_path.suffix.lower() != ".pdf":
        print(f"Error: Expected a .pdf file, got: {pdf_path.name}", file=sys.stderr)
        sys.exit(1)

    manifest = load_manifest()
    file_hash = sha256_of_file(pdf_path)
    existing = next(
        (e for e in manifest["extractions"] if e["sha256"] == file_hash),
        None,
    )

    if existing and not args.force:
        print(f"Already extracted: {existing['filename']} ({existing['extracted_at']})")
        for out in existing.get("outputs", []):
            print(f"  [{out['status']}] {out['draft_file']}")
        print("\nUse --force to re-extract.")
        sys.exit(0)

    print(f"Reading: {pdf_path.name}")
    pages = extract_pages(pdf_path)
    print(f"  {len(pages)} page(s) read.")

    missions = detect_missions(pages)
    total = len(missions)
    print(f"  {total} mission(s) detected.")

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    pdf_slug = _to_slug(pdf_path.stem)
    outputs: list[dict] = []

    for i, (title, text) in enumerate(missions, start=1):
        if total == 1:
            draft_name = f"_draft_{pdf_slug}.md"
        else:
            draft_name = f"_draft_{pdf_slug}-{i:02d}.md"

        draft_path = output_dir / draft_name
        draft_path.write_text(
            generate_draft(pdf_path.name, i, total, title, text),
            encoding="utf-8",
        )

        outputs.append({
            "draft_file": str(draft_path.relative_to(REPO_ROOT)),
            "detected_title": title,
            "status": "needs-review",
        })

        label = f" — {title!r}" if title else ""
        print(f"  Written: {draft_name}{label}")

    entry = {
        "filename": pdf_path.name,
        "sha256": file_hash,
        "extracted_at": datetime.now(timezone.utc).isoformat(),
        "page_count": len(pages),
        "missions_detected": total,
        "status": "needs-review",
        "outputs": outputs,
    }

    if existing:
        manifest["extractions"] = [
            e if e["sha256"] != file_hash else entry
            for e in manifest["extractions"]
        ]
    else:
        manifest["extractions"].append(entry)

    save_manifest(manifest)
    print(f"\nManifest updated: {MANIFEST_PATH.relative_to(REPO_ROOT)}")
    print(
        f"\nNext steps:\n"
        f"  1. Review the _draft_ file(s) in {output_dir.relative_to(REPO_ROOT)}/\n"
        f"  2. Restructure content and move to the appropriate summaries/missions/ subdirectory\n"
        f"  3. Update manifest.json — set each output's status to 'approved' when done\n"
        f"\nRun with --list to see all extractions."
    )


if __name__ == "__main__":
    main()
