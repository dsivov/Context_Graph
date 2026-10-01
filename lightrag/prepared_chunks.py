"""Explicit chunk plans with document-revision ownership, not global text IDs."""
import hashlib


def prepared_chunk_id(doc_id: str, text: str) -> str:
    return "chunk-" + hashlib.sha256((doc_id + "\0" + text).encode()).hexdigest()


def build_prepared_chunks(texts, doc_id, file_path, tokenizer, token_limit):
    if not texts or len(texts) > 1000:
        raise ValueError("Prepared chunk count is outside the budget")
    chunks = {}
    for text in texts:
        if not isinstance(text, str) or not text.strip():
            raise ValueError("Empty prepared chunk")
        tokens = len(tokenizer.encode(text))
        if tokens > token_limit:
            raise ValueError("Prepared unit exceeds chunk budget; cannot split its context")
        key = prepared_chunk_id(doc_id, text)
        if key not in chunks:
            chunks[key] = dict(content=text, tokens=tokens,
                               chunk_order_index=len(chunks), full_doc_id=doc_id,
                               file_path=file_path, llm_cache_list=[])
    return chunks
