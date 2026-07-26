# Portfolio content

Hand-curated, site-facing copy — the boiled-down cut of the deep engineering docs. This is the
**only** narrative content the portfolio site consumes, and the flow is deliberately one-way:

```
ARCHITECTURE.md ─(you edit)→ architecture_edit.md ─(converter)→ reports/portfolio/architecture.json ─(you copy)→ site
LEARNING.md     ─(you edit)→ learning_edit.md      ─(converter)→ reports/portfolio/journey.json      ─(you copy)→ site
```

- **`*_edit.md`** are yours to write. The deep docs (`ARCHITECTURE.md`, `LEARNING.md`) stay as the
  engineering reference; these are the trimmed portfolio versions.
- The converter is dumb on purpose: it splits YAML front-matter and `##` sections and copies each
  section's **raw markdown through unchanged**. It never rewrites prose. Styling lives in the site.
- Output JSON is the styling-agnostic **data contract** — the same split the platform already uses
  for `evidence.json` / `drift.json`.

## Regenerate the JSON

```bash
uv run python -m quickdraw.portfolio.export           # all docs → reports/portfolio/
uv run python -m quickdraw.portfolio.export --doc journey
```

Then copy `reports/portfolio/*.json` into the portfolio site's source and let it render.

## Edit-file conventions

- **Front matter** (`--- ... ---`, YAML) → top-level fields on the JSON (e.g. `title`, `subtitle`).
- **`## Heading`** starts a section. Content before the first `##` becomes an optional `lede`.
- **`architecture_edit.md`** (`sections` kind) → `{ title, subtitle, lede?, sections: [{ id, title, body_md }] }`.
- **`learning_edit.md`** (`journey` kind) → entries; each heading must be `## YYYY-MM-DD — headline`
  → `{ date, title, slug, body_md }`. A heading that isn't in that shape fails loudly.
