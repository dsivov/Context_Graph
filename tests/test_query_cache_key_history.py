"""
Unit tests for conversation history in the query response cache key.

Conversation history is sent to the LLM as context (``operate.py`` passes
``query_param.conversation_history`` straight to ``history_messages``), so two
calls sharing a query but differing in history can have different correct
answers. Before this fix the query cache key omitted history entirely, so the
second caller to utter a sentence received the first caller's answer.

The lower-level LLM cache in ``use_llm_func_with_cache`` already folded history
into its prompt hash; only the query-response layer was affected.
"""

import pytest

# Mark all tests as offline (no external dependencies)
pytestmark = pytest.mark.offline


class TestComputeHistoryHash:
    """Test suite for compute_history_hash."""

    def test_empty_history_is_empty_string(self):
        """No history must hash to "" so existing single-turn keys are preserved."""
        from lightrag.utils import compute_history_hash

        assert compute_history_hash([]) == ""
        assert compute_history_hash(None) == ""

    def test_backwards_compatible_for_single_turn(self):
        """Appending the digest for empty history must not change the key.

        ``compute_args_hash`` concatenates its arguments, so an empty component
        is a no-op. This is what keeps the fix from invalidating every cached
        single-turn answer in a running deployment.
        """
        from lightrag.utils import compute_args_hash, compute_history_hash

        before = compute_args_hash("hybrid", "what is the warranty?", 10)
        after = compute_args_hash(
            "hybrid", "what is the warranty?", 10, compute_history_hash([])
        )
        assert before == after

    def test_different_history_changes_the_hash(self):
        """The defect this fixes: two conversations must not collide."""
        from lightrag.utils import compute_history_hash

        margaret = [
            {"role": "user", "content": "I can't hear the dialogue on my TV."},
            {"role": "assistant", "content": "Consider the Bose TV Speaker."},
        ]
        harold = [
            {"role": "user", "content": "I can't hear the dialogue on my TV."},
            {"role": "assistant", "content": "Consider the Samsung soundbar."},
        ]
        assert compute_history_hash(margaret) != compute_history_hash(harold)

    def test_same_history_is_stable(self):
        """Equal history must produce an equal digest, so caching still works."""
        from lightrag.utils import compute_history_hash

        history = [
            {"role": "user", "content": "Will that work with my television?"},
            {"role": "assistant", "content": "Yes, over optical."},
        ]
        assert compute_history_hash(history) == compute_history_hash(list(history))

    def test_role_is_part_of_the_digest(self):
        """Who said what matters; swapping speakers is a different conversation."""
        from lightrag.utils import compute_history_hash

        a = [{"role": "user", "content": "hello"}]
        b = [{"role": "assistant", "content": "hello"}]
        assert compute_history_hash(a) != compute_history_hash(b)

    def test_turn_order_matters(self):
        """History is a sequence, not a set."""
        from lightrag.utils import compute_history_hash

        a = [
            {"role": "user", "content": "first"},
            {"role": "user", "content": "second"},
        ]
        assert compute_history_hash(a) != compute_history_hash(list(reversed(a)))

    def test_tolerates_missing_keys_and_non_dicts(self):
        """Malformed history must not raise — a cache key is not a validator."""
        from lightrag.utils import compute_history_hash

        assert compute_history_hash([{}]) != ""
        assert compute_history_hash([{"role": "user"}]) != ""
        assert compute_history_hash(["not a dict"]) != ""

    def test_unicode_is_handled(self):
        """compute_args_hash handles surrogates; the wrapper must not undo that."""
        from lightrag.utils import compute_history_hash

        assert compute_history_hash([{"role": "user", "content": "café ☕"}])


class TestQueryCacheKeyUsesHistory:
    """The keys built in operate.py must actually differ across conversations."""

    @staticmethod
    def _key(history):
        """Mirror of the kg_query cache key, varying only conversation history."""
        from lightrag.utils import compute_args_hash, compute_history_hash

        return compute_args_hash(
            "hybrid",
            "Will that work with my television?",
            "Multiple Paragraphs",
            40,
            10,
            6000,
            8000,
            30000,
            "hl",
            "ll",
            "",
            True,
            "annotated",
            compute_history_hash(history),
        )

    def test_two_conversations_do_not_collide(self):
        bose = [{"role": "assistant", "content": "Consider the Bose TV Speaker."}]
        samsung = [{"role": "assistant", "content": "Consider the Samsung soundbar."}]
        assert self._key(bose) != self._key(samsung)

    def test_no_history_matches_the_pre_fix_key(self):
        """A single-turn query keeps the key it had before this change."""
        from lightrag.utils import compute_args_hash

        pre_fix = compute_args_hash(
            "hybrid",
            "Will that work with my television?",
            "Multiple Paragraphs",
            40,
            10,
            6000,
            8000,
            30000,
            "hl",
            "ll",
            "",
            True,
            "annotated",
        )
        assert self._key([]) == pre_fix
