# app/services/rag_service.py

import os
import re
import textwrap
from typing import Optional, List
from dataclasses import dataclass
import numpy as np

from app.services.retrieval_service import hybrid_search, RetrievedChunk
from app.services.llm_service import HuggingFaceRouterLLMClient, ChatMessage, ANSWER_GENERATION_PROMPT
from app.services.embedding_service import generate_embedding


# ── CONFIG ─────────────────────────────────────────────────
MAX_CONTEXT_CHARS = 10000
MAX_HISTORY_TURNS = 6
SEMANTIC_TOP_K = 6


# ── DTO ────────────────────────────────────────────────────

@dataclass
class SourceMetadata:
    id: str
    document_id: str
    file_name: str
    page_number: int
    chunk_id: int
    bm25_rank: Optional[int]
    hnsw_rank: Optional[int]
    rrf_score: float


@dataclass
class ChatResponse:
    answer: str
    sources: List[str]
    metadata: dict
    conversation_id: Optional[str] = None


# ── MEMORY (FIXED) ─────────────────────────────────────────

class ConversationHistory:

    def __init__(self):
        self.store: dict[str, list[ChatMessage]] = {}
        self.embeddings: dict[str, list] = {}

    def append(self, cid: str, msg: ChatMessage):
        if cid not in self.store:
            self.store[cid] = []
            self.embeddings[cid] = []

        self.store[cid].append(msg)

        # ✅ FIX 1: Use correct embedding type
        if msg.role == "user":
            emb = generate_embedding(msg.content, is_query=True)
        else:
            emb = generate_embedding(msg.content, is_query=False)

        self.embeddings[cid].append(emb)

        # cap history
        max_msgs = MAX_HISTORY_TURNS * 2
        if len(self.store[cid]) > max_msgs:
            self.store[cid] = self.store[cid][-max_msgs:]
            self.embeddings[cid] = self.embeddings[cid][-max_msgs:]

    def get_recent(self, cid: str, n: int = MAX_HISTORY_TURNS) -> List[ChatMessage]:
        msgs = self.store.get(cid, [])
        return msgs[-(n * 2):]

    def semantic_search(self, cid: str, query: str, top_k: int = SEMANTIC_TOP_K) -> List[ChatMessage]:
        if cid not in self.store or not self.store[cid]:
            return []

        query_emb = generate_embedding(query, is_query=True)
        if query_emb is None:
            return self.get_recent(cid)

        query_vec = np.array(query_emb)
        scores = []

        for i, emb in enumerate(self.embeddings[cid]):
            if emb is None:
                continue

            emb_vec = np.array(emb)

            # ✅ FIX 2: embeddings already normalized → dot product only
            sim = np.dot(query_vec, emb_vec)

            scores.append((sim, i))

        scores.sort(reverse=True, key=lambda x: x[0])

        selected: List[ChatMessage] = []
        used: set[int] = set()

        for _, idx in scores[:top_k]:
            if idx in used:
                continue

            selected.append(self.store[cid][idx])
            used.add(idx)

            # keep conversational continuity
            if idx + 1 < len(self.store[cid]) and (idx + 1) not in used:
                selected.append(self.store[cid][idx + 1])
                used.add(idx + 1)

        return selected

    def clear(self, cid: str):
        self.store.pop(cid, None)
        self.embeddings.pop(cid, None)


_history = ConversationHistory()


# ── CONTEXT BUILDER ────────────────────────────────────────

class ContextBuilder:

    @staticmethod
    def build(chunks: List[RetrievedChunk]) -> str:
        if not chunks:
            return "No relevant documents were found."

        parts = []
        budget = MAX_CONTEXT_CHARS

        for i, chunk in enumerate(chunks, start=1):
            file_name = _basename(chunk.file_name)
            header = f"[Source {i}] {file_name} — page {chunk.page_number}"
            body = getattr(chunk, "llm_content", chunk.content)

            entry = f"{header}\n{body}\n"

            if len(entry) > budget:
                entry = entry[:budget]

            parts.append(entry)
            budget -= len(entry)

            if budget <= 0:
                break

        return "\n---\n".join(parts)


# ── HELPERS ────────────────────────────────────────────────

def _basename(path: str) -> str:
    return os.path.basename(path) if path else path


def _clean_answer(text: str) -> str:
    text = text.replace("\\n", " ")
    text = re.sub(r"\n+", " ", text)
    text = re.sub(r" {2,}", " ", text)
    return text.strip()


def _format_history(history: List[ChatMessage]) -> str:
    if not history:
        return "No prior conversation."
    lines = []
    for msg in history:
        role = "User" if msg.role == "user" else "Assistant"
        lines.append(f"{role}: {msg.content}")
    return "\n".join(lines)


# ── LLM CLIENT ─────────────────────────────────────────────

_llm = HuggingFaceRouterLLMClient()


# ── MAIN RAG FUNCTION ──────────────────────────────────────

def rag_chat(
    user_message: str,
    conversation_id: str = "default",
    top_k: int = 3,
    selected_pdf_ids: List[str] = [],    # NEW
) -> ChatResponse:

    # ── 1. Get recent history ─────────
    recent_history = _history.get_recent(conversation_id)

    # ── 2. Query rewriting ────────────
    if recent_history:
        standalone_query = _llm.rewrite_query(recent_history, user_message)
    else:
        standalone_query = user_message

    # ── 3. Retrieve documents using the standalone query ─────
    chunks = hybrid_search(standalone_query, top_k=top_k)

    # ✅ FIX 3: fallback if retrieval fails (critical for first query)
    if not chunks:
        chunks = hybrid_search(user_message, top_k=top_k)

    # ── 4. Filter chunks to selected documents (post-retrieval, pre-context) ──
    # If the user selected specific PDFs, discard chunks from other documents
    # before building the LLM context. Filtering here (rather than in SQL) keeps
    # the retrieval layer clean and lets ranking/reranking see the full corpus.
    if selected_pdf_ids:
        selected_basenames = {os.path.basename(f) for f in selected_pdf_ids}
        chunks = [c for c in chunks if _basename(c.file_name) in selected_basenames]
    # ── 5. Context ────────────────────
    context = ContextBuilder.build(chunks)

    # ── 6. Semantic history ───────────
    semantic_history = _history.semantic_search(
        conversation_id,
        standalone_query,
        top_k=SEMANTIC_TOP_K,
    )

    history_text = _format_history(semantic_history)

    # ── 7. Prompt ─────────────────────
    system_prompt = (
        ANSWER_GENERATION_PROMPT
        + f"\n\nCONVERSATION HISTORY:\n{history_text}"
        + f"\n\nDOCUMENT CONTEXT:\n{context}"
    )
    print("\n" + "="*80)
    print("🧠 FINAL SYSTEM PROMPT SENT TO LLM")
    print("="*80)
    print(system_prompt)

    print("\n" + "="*80)
    print("💬 SEMANTIC HISTORY PASSED TO LLM")
    print("="*80)
    for msg in semantic_history:
        print(f"{msg.role.upper()}: {msg.content}")

    print("\n" + "="*80)
    print("❓ USER MESSAGE")
    print("="*80)
    print(user_message)
    print("="*80 + "\n")

    # ── 8. LLM ────────────────────────
    raw_answer = _llm.generate(system_prompt, semantic_history, user_message)

    answer = _clean_answer(raw_answer)

    # ── 9. Store history ──────────────
    _history.append(conversation_id, ChatMessage("user", user_message))
    _history.append(conversation_id, ChatMessage("assistant", answer))

    # ── 10. Metadata ───────────────────
    source_files = list({_basename(c.file_name) for c in chunks})

    metadata = {
        "chunks": [
            {
                "id": c.id,
                "document_id": c.document_id,
                "file_name": _basename(c.file_name),
                "page_number": c.page_number,
                "chunk_id": c.chunk_id,

                "bm25_rank": c.bm25_rank,
                "hnsw_rank": c.hnsw_rank,

                "bm25_score": c.bm25_score,     # 🔥 ADD
                "hnsw_score": c.hnsw_score,     # 🔥 ADD

                "rrf_score": c.rrf_score,
                "rerank_score": c.rerank_score, # 🔥 OPTIONAL (VERY USEFUL)

                "content": c.content
            }
            for c in chunks
        ]
    }

    return ChatResponse(
        answer=answer,
        sources=source_files,
        metadata=metadata,
        conversation_id=conversation_id,
    )


def clear_history(conversation_id: str):
    _history.clear(conversation_id)