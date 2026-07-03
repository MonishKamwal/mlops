"""Dataset download, preprocessing (shared with serving), and validation.

The serve-time entry points live in :mod:`quickdraw.data.preprocess`
(``strokes_to_model_input``, ``png_to_model_input``). The serving app must import
these — never reimplement them; that one code path is the train/serve-skew guarantee.
"""
