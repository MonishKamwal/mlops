"""Portfolio content pipeline: hand-curated markdown → JSON contracts for the site.

The deep docs (``ARCHITECTURE.md``, ``LEARNING.md``) are the engineering reference. The
portfolio site never reads them directly — it reads the JSON emitted by
:mod:`quickdraw.portfolio.export`, generated from the boiled-down *edit files* under
``portfolio/`` that a human curates by hand.
"""
