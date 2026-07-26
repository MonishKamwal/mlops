"""Tests for the portfolio content pipeline (markdown edit files → JSON contracts)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from quickdraw.portfolio import export

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_slugify_strips_numbering_and_punctuation():
    assert export.slugify("1. System overview") == "system-overview"
    assert export.slugify("The retrain flywheel") == "the-retrain-flywheel"
    assert export.slugify("`dvc pull` & the lockfile") == "dvc-pull-the-lockfile"


def test_strip_numbering_only_touches_leading_list_number():
    assert export.strip_numbering("11. The portfolio cut") == "The portfolio cut"
    assert export.strip_numbering("Version 2.0 notes") == "Version 2.0 notes"


def test_split_front_matter_parses_yaml_and_returns_body():
    meta, body = export.split_front_matter("---\ntitle: Arch\nsubtitle: hi\n---\n## A\n\ntext")
    assert meta == {"title": "Arch", "subtitle": "hi"}
    assert body.strip().startswith("## A")


def test_split_front_matter_absent_is_empty_meta():
    meta, body = export.split_front_matter("## A\n\ntext")
    assert meta == {}
    assert body == "## A\n\ntext"


def test_split_front_matter_unterminated_raises():
    with pytest.raises(ValueError, match="unterminated"):
        export.split_front_matter("---\ntitle: Arch\n## A\n")


def test_split_sections_captures_lede_and_sections():
    lede, sections = export.split_sections("intro line\n\n## One\n\nbody one\n\n## Two\n\nbody two")
    assert lede == "intro line"
    assert [h for h, _ in sections] == ["One", "Two"]
    assert sections[0][1] == "body one"


def test_split_sections_ignores_h2_inside_a_code_fence():
    md = "## Real\n\n```bash\n## not a heading\necho hi\n```\n\nafter"
    lede, sections = export.split_sections(md)
    assert lede == ""
    assert len(sections) == 1
    assert sections[0][0] == "Real"
    assert "## not a heading" in sections[0][1]


def test_convert_text_sections_shape():
    text = "---\ntitle: Architecture\n---\nlede here\n\n## 1. System overview\n\nfour planes"
    doc = export.convert_text(text, "sections")
    assert doc["title"] == "Architecture"
    assert doc["lede"] == "lede here"
    assert doc["sections"] == [
        {"id": "system-overview", "title": "System overview", "body_md": "four planes"}
    ]


def test_convert_text_journey_parses_date_and_headline():
    text = "---\ntitle: Journey\n---\n## 2026-07-20 — The quality gate\n\ndecouple ship from best"
    doc = export.convert_text(text, "journey")
    assert doc["entries"] == [
        {
            "date": "2026-07-20",
            "title": "The quality gate",
            "slug": "the-quality-gate",
            "body_md": "decouple ship from best",
        }
    ]


def test_convert_text_journey_bad_heading_raises():
    with pytest.raises(ValueError, match="YYYY-MM-DD"):
        export.convert_text("## Not a dated heading\n\nbody", "journey")


def test_convert_text_unknown_kind_raises():
    with pytest.raises(ValueError, match="unknown doc kind"):
        export.convert_text("## A\n\nb", "sideways")


def test_contract_round_trips_through_json():
    doc = export.convert_text("---\ntitle: T\n---\n## A\n\nbody", "sections")
    assert json.loads(json.dumps(doc)) == doc


@pytest.mark.parametrize("name", sorted(export.DOCS))
def test_seeded_edit_files_convert(name):
    """The checked-in edit files parse cleanly and yield a non-empty contract."""
    spec = export.DOCS[name]
    doc = export.convert_file(REPO_ROOT / spec.src, spec.kind)
    items = doc.get("entries") if spec.kind == "journey" else doc.get("sections")
    assert items, f"{name} produced no {spec.kind}"
    assert doc.get("title")


def test_main_writes_all_contracts(tmp_path):
    out = tmp_path / "out"
    rc = export.main(["--root", str(REPO_ROOT), "--out-dir", str(out)])
    assert rc == 0
    for spec in export.DOCS.values():
        written = json.loads((out / spec.out).read_text())
        assert written["title"]
