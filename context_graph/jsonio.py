"""Tiny, dependency-free JSON helpers shared across Context Graph modules.

This module deliberately imports nothing from ``lightrag`` or from
``context_graph.core``. Several ``context_graph`` submodules (dedup, community,
connectivity, ontology, rules, webingest) need ``_extract_json_object`` to parse
LLM output; keeping it in this leaf module lets them import it without pulling in
the heavyweight :class:`~context_graph.core.ContextGraph` (which would create an
import cycle through ``lightrag``).
"""

from __future__ import annotations

import json


def _extract_json_object(text: str) -> dict | None:
    """Best-effort extraction of a JSON object from an LLM response.

    LLMs frequently wrap JSON in a ``` or ```json code fence, or surround it
    with prose. This recovers the object in those cases and returns it as a
    dict, or ``None`` if no JSON object can be parsed (callers treat ``None``
    as 'unparseable' and fall back). Returning ``None`` for a non-object
    (scalar/array) avoids ``AttributeError`` on a later ``.get(...)``.
    """
    if not text:
        return None
    s = text.strip()
    # Strip a leading code fence + optional language tag, then drop the
    # trailing fence (and anything after it).
    if s.startswith("```"):
        s = s[3:]
        if s[:4].lower() == "json":
            s = s[4:]
        s = s.split("```", 1)[0].strip()
    try:
        obj = json.loads(s)
    except (json.JSONDecodeError, TypeError):
        # Fall back to the outermost {...} span.
        start, end = s.find("{"), s.rfind("}")
        if start == -1 or end <= start:
            return None
        try:
            obj = json.loads(s[start : end + 1])
        except (json.JSONDecodeError, TypeError):
            return None
    return obj if isinstance(obj, dict) else None
