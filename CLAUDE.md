# CLAUDE.md

Start here each session:

1. Read `MEMORY.md` — current state, facts not in the code, and the exact next step.
2. `PLAN.md` is the source of truth for architecture, decisions (§2), and phases (§5).

Conventions:

- Update MEMORY.md ("Current state", "Immediate next step", progress log) in the same
  commit as the work it describes.
- When a task teaches a non-obvious concept, add a dated entry to `LEARNING.md` (newest
  first) — it is the learning journal and feeds the portfolio's Journey section.
- One-time/admin setup happens via web UI by the user (give click paths); platform
  resources live in Terraform; automation in GitHub Actions.
- Python is uv-managed: `uv sync`, `uv run pytest`, `uv run ruff check .`. Pre-commit is
  installed; CI runs ruff + format check + pytest.
- Trunk-based development; keep commits small and story-shaped — this is a public
  portfolio repo, so the history is part of the product.
