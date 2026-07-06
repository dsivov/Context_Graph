"""Static-vector similarity matching for the Business Rules Engine.

The field-level ``sim()`` predicate (``docs/CLOSING_THE_GAPS.html`` §04). It
backs the *soft* half of a business rule — matching paraphrased, LLM-extracted
free-text fields by meaning — while structured fields (numbers, dates, flags)
stay on exact predicates.

Design (validated empirically against ``potion-retrieval-32M``):

* **Static embeddings** via `model2vec <https://github.com/MinishLab/model2vec>`_
  — numpy-only at inference, CPU, sub-millisecond per short string, MIT-licensed
  and deterministic. Cheap enough to run synchronously on every decision write.
* **Concept catalog, not ad-hoc anchors.** A concept (e.g. ``APPROVAL``) is
  defined by *several* example phrases and a field is scored by the **maximum**
  cosine over those phrases. Single-anchor matching is too weak and
  polarity-blind (a true synonym can score below an antonym); the multi-phrase
  catalog separates cleanly.
* **Topical routing only.** Similarity answers *"is this about approvals?"* — it
  is deliberately *not* used for polarity (approved vs. rejected), which belongs
  on structured fields.
* **Determinism.** The model id is pinned and surfaced; embeddings are behaviour,
  so a rule that passed yesterday must not silently flip today.

Backends are injectable (:class:`SimilarityBackend`) so the catalog logic is
unit-testable offline without loading a model.

Note: ``lightrag`` is a reused dependency — only its generic ``logger`` helper is
imported here. Everything else in this module is Context Graph's own code.
"""

from __future__ import annotations

import re
import threading
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Protocol, Sequence

import numpy as np

from lightrag.utils import logger

# ─────────────────────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────────────────────

DEFAULT_MODEL_ID = "minishlab/potion-retrieval-32M"
"""Pinned model. Static, retrieval-tuned, 512-dim, ~30 MB, MIT. See §04."""

DEFAULT_MATCH_THRESHOLD = 0.4
"""Default cosine cut for :func:`make_similar_predicate`.

Empirically this sits inside a wide separation gap (non-matches ceil ~0.24,
matches floor ~0.53 on the approval concept). It is a *starting point only* —
real thresholds should be calibrated per concept from rule fixtures (§05).
"""

_WORD_SPLIT_RE = re.compile(r"[_\-/]+")
_WS_RE = re.compile(r"\s+")


def _normalize_text(text: str) -> str:
    """Light, deterministic normalisation applied to every encoded string.

    Splits ``snake_case`` / ``kebab-case`` identifiers into words and collapses
    whitespace so that ``"GRANTED_APPROVAL"`` and ``"granted approval"`` land in
    the same region of the embedding space. Case is left to the (uncased) model.
    """
    text = _WORD_SPLIT_RE.sub(" ", text)
    return _WS_RE.sub(" ", text).strip()


def _l2_normalize(matrix: np.ndarray) -> np.ndarray:
    """Row-wise L2 normalisation so dot product equals cosine similarity."""
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    return matrix / (norms + 1e-9)


# ─────────────────────────────────────────────────────────────────────────────
# Backends
# ─────────────────────────────────────────────────────────────────────────────


class SimilarityUnavailable(RuntimeError):
    """The similarity backend cannot run (model2vec missing, or weights can't load).

    Typed so the engine can degrade *honestly* — report "similarity check
    unavailable" once, rather than leaking a per-rule ImportError (with pip
    instructions) that reads like reuse advice on an unrelated action.
    """


class SimilarityBackend(Protocol):
    """Encodes text to vectors. Injectable so the catalog is testable offline."""

    @property
    def model_id(self) -> str:
        ...

    def encode(self, texts: Sequence[str]) -> np.ndarray:
        """Return a ``(len(texts), dim)`` float32 array (need not be normalised)."""
        ...


class Model2VecBackend:
    """`model2vec` static-embedding backend. Lazily loads the model on first use.

    The model is loaded once and shared (thread-safe). ``model2vec`` and the
    pinned model weights must be installed/available; install via the optional
    extra: ``pip install -e ".[rules]"``.
    """

    def __init__(self, model_id: str = DEFAULT_MODEL_ID) -> None:
        self._model_id = model_id
        self._model = None
        self._lock = threading.Lock()

    @property
    def model_id(self) -> str:
        return self._model_id

    def _ensure_model(self):
        if self._model is None:
            with self._lock:
                if self._model is None:
                    try:
                        from model2vec import StaticModel
                    except ImportError as e:  # pragma: no cover - import guard
                        raise SimilarityUnavailable(
                            "model2vec is not installed — similarity matching is "
                            'unavailable. Install it with: pip install -e ".[rules]"'
                        ) from e
                    logger.info(
                        f"Loading similarity model '{self._model_id}' (model2vec, static)"
                    )
                    try:
                        self._model = StaticModel.from_pretrained(self._model_id)
                    except Exception as e:  # weights unreachable / offline
                        raise SimilarityUnavailable(
                            f"could not load similarity model '{self._model_id}': {e}"
                        ) from e
        return self._model

    def encode(self, texts: Sequence[str]) -> np.ndarray:
        model = self._ensure_model()
        return np.asarray(model.encode(list(texts)), dtype=np.float32)


# ─────────────────────────────────────────────────────────────────────────────
# Concept catalog
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class Concept:
    """A named semantic concept defined by a few example phrases.

    A field is scored against the concept by the **maximum** cosine similarity
    over the (L2-normalised) phrase embeddings.
    """

    name: str
    phrases: List[str]
    _matrix: Optional[np.ndarray] = field(default=None, repr=False, compare=False)

    def is_compiled(self) -> bool:
        return self._matrix is not None

    def compile(self, backend: SimilarityBackend) -> None:
        """Embed and cache the phrase matrix once (anchors are static)."""
        if not self.phrases:
            raise ValueError(f"Concept '{self.name}' has no example phrases")
        normed = [_normalize_text(p) for p in self.phrases]
        self._matrix = _l2_normalize(backend.encode(normed))

    def score_vector(self, vec: np.ndarray) -> float:
        """Max cosine of an already-L2-normalised query vector vs. the phrases."""
        if self._matrix is None:
            raise RuntimeError(f"Concept '{self.name}' is not compiled")
        return float((self._matrix @ vec.ravel()).max())


class ConceptCatalog:
    """A workspace-scoped catalogue of named concepts for fuzzy field matching.

    Concepts are compiled (embedded) lazily on first scoring. Query-vector
    encoding is memoised, since field values (relation types, channels) repeat.
    """

    def __init__(
        self,
        backend: Optional[SimilarityBackend] = None,
        *,
        cache_size: int = 4096,
    ) -> None:
        self._backend = backend if backend is not None else Model2VecBackend()
        self._concepts: Dict[str, Concept] = {}
        self._vec_cache: "Dict[str, np.ndarray]" = {}
        self._cache_size = cache_size
        self._lock = threading.Lock()

    # -- definition ---------------------------------------------------------

    @property
    def model_id(self) -> str:
        return self._backend.model_id

    def define(self, name: str, phrases: Sequence[str]) -> "ConceptCatalog":
        """Define (or replace) a concept. Returns self for chaining."""
        key = name.strip().upper()
        if not key:
            raise ValueError("Concept name must be non-empty")
        self._concepts[key] = Concept(name=key, phrases=list(phrases))
        return self

    def define_many(self, concepts: Dict[str, Sequence[str]]) -> "ConceptCatalog":
        for name, phrases in concepts.items():
            self.define(name, phrases)
        return self

    def names(self) -> List[str]:
        return sorted(self._concepts)

    def has(self, name: str) -> bool:
        return name.strip().upper() in self._concepts

    # -- scoring ------------------------------------------------------------

    def _encode_query(self, text: str) -> np.ndarray:
        """Encode + L2-normalise a single query string, with a small cache."""
        norm = _normalize_text(text)
        cached = self._vec_cache.get(norm)
        if cached is not None:
            return cached
        vec = _l2_normalize(self._backend.encode([norm]))[0]
        with self._lock:
            if len(self._vec_cache) >= self._cache_size:
                self._vec_cache.clear()  # simple bounded cache
            self._vec_cache[norm] = vec
        return vec

    def score(self, text: Optional[str], concept_name: str) -> float:
        """Similarity of ``text`` to ``concept_name`` in ``[-1, 1]``.

        Returns ``0.0`` for empty/None input (a missing field matches nothing).
        Raises ``KeyError`` for an unknown concept — a rule referencing an
        undefined concept is a bug that should surface in dry-run (§05).
        """
        key = concept_name.strip().upper()
        concept = self._concepts.get(key)
        if concept is None:
            raise KeyError(
                f"Unknown concept '{concept_name}'. Defined: {self.names()}"
            )
        if not text or not str(text).strip():
            return 0.0
        if not concept.is_compiled():
            concept.compile(self._backend)
        return concept.score_vector(self._encode_query(str(text)))

    def fingerprint(self) -> Dict[str, object]:
        """Determinism record: model id + concept definitions (for pinning/audit)."""
        return {
            "model_id": self.model_id,
            "concepts": {n: list(c.phrases) for n, c in sorted(self._concepts.items())},
        }


# ─────────────────────────────────────────────────────────────────────────────
# Predicate factories (for business_rule_engine.register_function)
# ─────────────────────────────────────────────────────────────────────────────


def make_sim_predicate(catalog: ConceptCatalog) -> Callable[[Optional[str], str], float]:
    """Build the ``sim(value, concept_name)`` predicate bound to *catalog*.

    Register the returned callable into a ``RuleParser`` so rules can write::

        when  sim(relation_type, "APPROVAL") > 0.4  and  amount > 10000

    Returns a **float** so the threshold stays visible in the rule text.
    """

    def sim(value: Optional[str], concept_name: str) -> float:
        return catalog.score(value, concept_name)

    sim.__name__ = "sim"
    return sim


def make_similar_predicate(
    catalog: ConceptCatalog,
    default_threshold: float = DEFAULT_MATCH_THRESHOLD,
) -> Callable[..., bool]:
    """Build a boolean ``similar(value, concept_name, threshold=...)`` convenience.

    Prefer :func:`make_sim_predicate` in rules (keeps the threshold explicit);
    this is a convenience for callers that want a plain boolean.
    """

    def similar(
        value: Optional[str],
        concept_name: str,
        threshold: float = default_threshold,
    ) -> bool:
        return catalog.score(value, concept_name) >= threshold

    similar.__name__ = "similar"
    return similar
