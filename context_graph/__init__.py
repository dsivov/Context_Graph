"""Context Graph (CG) — first-class project code.

This top-level package holds CG-owned functionality. ``lightrag`` is a reused
dependency library; everything that is *ours* lives here, not inside ``lightrag``.

Current modules:

* :mod:`context_graph.rules` — the Business Rules Engine layer, starting with the
  field-level ``sim()`` fuzzy-matching predicate (see
  ``docs/CLOSING_THE_GAPS.html`` §03–§05).
"""
