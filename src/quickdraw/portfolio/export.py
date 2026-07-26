"""Convert hand-curated portfolio markdown into the JSON contracts the site renders.

The pipeline, mirroring the data-vs-presentation split in :mod:`quickdraw.evidence.export`::

    ARCHITECTURE.md --(human edits)--> portfolio/architecture_edit.md --(here)--> architecture.json
    LEARNING.md     --(human edits)--> portfolio/learning_edit.md     --(here)--> journey.json

The deep docs are the engineering reference; the *edit files* are the portfolio-facing cut a
human curates; this module is the dumb reshaping in between. It splits YAML front-matter and
``##`` sections and copies each section's **raw markdown body through unchanged** — it never
rewrites prose. Styling lives in the site, curation lives in the edit files, and the JSON is the
styling-agnostic contract the two agree on. The site copies these JSON files into its own source.

Two document kinds:

* ``sections`` — an ordered list of ``{id, title, body_md}`` (architecture).
* ``journey``  — dated ``## YYYY-MM-DD — headline`` entries → ``{date, title, slug, body_md}``.
"""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass
from pathlib import Path

import yaml

# A ``## YYYY-MM-DD — headline`` journey heading. The separator may be an em/en dash or hyphen.
_JOURNEY_HEADING = re.compile(r"^(\d{4}-\d{2}-\d{2})\s*[—–-]\s*(.+)$")
_LEADING_NUMBER = re.compile(r"^\d+\.\s+")


@dataclass(frozen=True)
class DocSpec:
    """One editable doc: where it lives, what it's called on disk, and how to shape it."""

    src: Path
    out: str
    kind: str  # "sections" | "journey"


# The docs this pipeline knows about. ``src`` is relative to ``--root`` (repo root by default).
DOCS: dict[str, DocSpec] = {
    "architecture": DocSpec(
        src=Path("portfolio/architecture_edit.md"), out="architecture.json", kind="sections"
    ),
    "journey": DocSpec(src=Path("portfolio/learning_edit.md"), out="journey.json", kind="journey"),
}


def strip_numbering(title: str) -> str:
    """Drop a leading ``N.`` list number from a heading (``1. Overview`` → ``Overview``)."""
    return _LEADING_NUMBER.sub("", title).strip()


def slugify(text: str) -> str:
    """A stable URL/anchor slug: numbering stripped, lowercased, non-alphanumerics → ``-``."""
    text = strip_numbering(text).lower()
    return re.sub(r"[^a-z0-9]+", "-", text).strip("-")


def split_front_matter(text: str) -> tuple[dict, str]:
    """Peel an optional ``---`` YAML front-matter block off the top; return ``(meta, body)``."""
    if not text.startswith("---\n") and not text.startswith("---\r\n"):
        return {}, text
    lines = text.splitlines()
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            meta = yaml.safe_load("\n".join(lines[1:i])) or {}
            if not isinstance(meta, dict):
                raise ValueError("portfolio front matter must be a YAML mapping")
            return meta, "\n".join(lines[i + 1 :])
    raise ValueError("unterminated front-matter block (missing closing '---')")


def split_sections(body: str) -> tuple[str, list[tuple[str, str]]]:
    """Split markdown into an optional lede plus ``## `` sections.

    Fenced code blocks are honoured, so a ``## `` line *inside* a fence (e.g. a shell comment)
    is body, not a new section. Returns ``(lede, [(heading, body_md), ...])`` with bodies stripped.
    """
    lede: list[str] = []
    sections: list[tuple[str, list[str]]] = []
    in_fence = False
    fence = ""
    for line in body.splitlines():
        stripped = line.lstrip()
        if stripped.startswith("```") or stripped.startswith("~~~"):
            marker = stripped[:3]
            if not in_fence:
                in_fence, fence = True, marker
            elif stripped.startswith(fence):
                in_fence = False
        elif not in_fence and line.startswith("## "):
            sections.append((line[3:].strip(), []))
            continue
        (sections[-1][1] if sections else lede).append(line)
    return (
        "\n".join(lede).strip(),
        [(heading, "\n".join(lines).strip()) for heading, lines in sections],
    )


def _journey_entry(heading: str, body_md: str) -> dict:
    match = _JOURNEY_HEADING.match(heading)
    if not match:
        raise ValueError(f"journey heading must be 'YYYY-MM-DD — headline', got: {heading!r}")
    headline = match.group(2).strip()
    return {
        "date": match.group(1),
        "title": headline,
        "slug": slugify(headline),
        "body_md": body_md,
    }


def convert_text(text: str, kind: str) -> dict:
    """Turn one edit-file's markdown into its JSON contract dict (pure; JSON-round-trippable)."""
    meta, body = split_front_matter(text)
    lede, raw_sections = split_sections(body)
    doc: dict = dict(meta)
    if lede:
        doc["lede"] = lede
    if kind == "sections":
        doc["sections"] = [
            {"id": slugify(heading), "title": strip_numbering(heading), "body_md": body_md}
            for heading, body_md in raw_sections
        ]
    elif kind == "journey":
        doc["entries"] = [_journey_entry(heading, body_md) for heading, body_md in raw_sections]
    else:
        raise ValueError(f"unknown doc kind: {kind!r}")
    return doc


def convert_file(src: Path, kind: str) -> dict:
    return convert_text(src.read_text(encoding="utf-8"), kind)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Convert portfolio edit docs into JSON contracts.")
    parser.add_argument("--root", default=".", help="repo root the edit files are relative to")
    parser.add_argument(
        "--out-dir", default="reports/portfolio", help="where to write the JSON contracts"
    )
    parser.add_argument(
        "--doc",
        choices=sorted(DOCS),
        action="append",
        help="convert only this doc (repeatable); default: all",
    )
    args = parser.parse_args(argv)

    root = Path(args.root)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    for name in args.doc or sorted(DOCS):
        spec = DOCS[name]
        data = convert_file(root / spec.src, spec.kind)
        dest = out_dir / spec.out
        dest.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        items = data.get("entries") if spec.kind == "journey" else data.get("sections")
        noun = "entries" if spec.kind == "journey" else "sections"
        print(f"{name}: {spec.src} → {dest} ({len(items or [])} {noun})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
