"""
Retrieval Service — Hybrid BM25 (sparse) + HNSW (dense) search
"""

import logging, re
from collections import Counter
from dataclasses import dataclass, field
from typing import Optional
from concurrent.futures import ThreadPoolExecutor

from app.db.db import Database
from app.services.embedding_service import generate_embedding
from app.services.re_ranker_service import rerank

logger = logging.getLogger(__name__)

# ── Tuning knobs ──────────────────────────────────────────────────────────────
DEFAULT_TOP_K: int = 20
DEFAULT_BM25_WEIGHT: float = 0.2
DEFAULT_HNSW_WEIGHT: float = 0.8
RRF_K: int = 60

@dataclass
class RetrievedChunk:
    """A single ranked result returned by hybrid search."""
    id: str
    document_id: str
    file_name: str
    page_number: int
    chunk_id: int
    content: str
    bm25_rank: Optional[float] = None   # NEW
    hnsw_rank: Optional[float] = None   # NEW
    bm25_score: Optional[float] = None   # NEW
    hnsw_score: Optional[float] = None   # NEW
    rrf_score: float = 0.0
    rerank_score: float = 0.0          # score assigned by CrossEncoder reranker
    llm_content: str = ""              # expanded context sent to LLM
    metadata: dict = field(default_factory=dict)

# ── BM25 retrieval (PostgreSQL full-text search) ──────────────────────────────

def _bm25_search(
    query: str,
    top_k: int = DEFAULT_TOP_K * 2,
) -> list[RetrievedChunk]:
    if not query or not query.strip():
        return []

    params = [query, query, top_k]

    sql = """
        SELECT
            id,
            document_id::text,
            file_name,
            page_number,
            chunk_id,
            content,
            ts_rank_cd(content_tsv, websearch_to_tsquery('english', %s)) AS bm25_score
        FROM embeddings
        WHERE content_tsv @@ websearch_to_tsquery('english', %s)
        ORDER BY bm25_score DESC
        LIMIT %s;
    """

    conn = Database.get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()
    except Exception as exc:
        logger.error(f"BM25 search failed: {exc}")
        return []
    finally:
        Database.return_connection(conn)

    results = []
    for row in rows:
        results.append(
        RetrievedChunk(
            id=row[0],
            document_id=row[1],
            file_name=row[2] or "",
            page_number=row[3] or 0,
            chunk_id=row[4] or 0,
            content=row[5] or "",
            bm25_score=row[6],   # ✅ ADD THIS
        )
    )
    # logger.info("------ BM25 RESULTS ------")
    # for i, r in enumerate(results):
    #     logger.info(f"[BM25 {i}] score={r.bm25_score} -> {r.content}")
    debug_file_distribution("BM25", results)
    return results


# ── HNSW retrieval (pgvector cosine similarity) ───────────────────────────────

def _hnsw_search(
    query: str,
    top_k: int = DEFAULT_TOP_K * 3,
) -> list[RetrievedChunk]:
    if not query or not query.strip():
        return []

    embedding = generate_embedding(query, is_query=True)
    if embedding is None:
        return []

    vec_str = "[" + ",".join(str(v) for v in embedding) + "]"

    params = [vec_str, vec_str, top_k]

    sql = """
        SELECT
            id, document_id::text, file_name, page_number, chunk_id, content,
            1 - (embedding <=> %s::vector) AS cosine_similarity
        FROM embeddings
        WHERE embedding IS NOT NULL
        ORDER BY embedding <=> %s::vector
        LIMIT %s;
    """

    conn = Database.get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()
    except Exception as exc:
        logger.error(f"HNSW search failed: {exc}")
        return []
    finally:
        Database.return_connection(conn)

    results = []
    for row in rows:
        results.append(
        RetrievedChunk(
            id=row[0],
            document_id=row[1],
            file_name=row[2] or "",
            page_number=row[3] or 0,
            chunk_id=row[4] or 0,
            content=row[5] or "",
            hnsw_score=row[6], 
        )
)
    # for i, r in enumerate(results):
    #     logger.info(f"[HNSW {i}] score={r.hnsw_score} -> {r.content}")
    
    debug_file_distribution("HNSW", results)
    return results

# ── Reciprocal Rank Fusion ────────────────────────────────────────────────────

def _reciprocal_rank_fusion(
    bm25_results: list[RetrievedChunk],
    hnsw_results: list[RetrievedChunk],
    bm25_weight: float = DEFAULT_BM25_WEIGHT,
    hnsw_weight: float = DEFAULT_HNSW_WEIGHT,
    rrf_k: int = RRF_K,
) -> list[RetrievedChunk]:

    merged: dict[str, RetrievedChunk] = {}

    # ✅ BM25 loop
    for rank, chunk in enumerate(bm25_results, start=1):
        if chunk.id not in merged:
            merged[chunk.id] = chunk
        else:
            # merge into existing
            existing = merged[chunk.id]
            existing.bm25_score = chunk.bm25_score

        merged_chunk = merged[chunk.id]

        merged_chunk.bm25_rank = rank
        merged_chunk.rrf_score += bm25_weight / (rrf_k + rank)

        # ✅ preserve bm25 score
        merged_chunk.bm25_score = chunk.bm25_score

    # ✅ HNSW loop
    for rank, chunk in enumerate(hnsw_results, start=1):
        if chunk.id not in merged:
            merged[chunk.id] = chunk
        else:
            existing = merged[chunk.id]
            existing.hnsw_score = chunk.hnsw_score

        merged_chunk = merged[chunk.id]

        merged_chunk.hnsw_rank = rank
        merged_chunk.rrf_score += hnsw_weight / (rrf_k + rank)

        # ✅ preserve hnsw score
        merged_chunk.hnsw_score = chunk.hnsw_score
    # logger.info("------ RRF FUSED RESULTS ------")
    # for i, r in enumerate(sorted(merged.values(), key=lambda c: c.rrf_score, reverse=True)):
    #     logger.info(
    #         f"[RRF {i}] score={r.rrf_score:.4f} | "
    #         f"bm25_rank={r.bm25_rank} hnsw_rank={r.hnsw_rank} | "
    #         f"text={r.content}"
    #     )
    
    return sorted(merged.values(), key=lambda c: c.rrf_score, reverse=True)

def normalize_query(q: str):
    q = q.lower()
    return re.sub(r'[^a-z0-9\s\.\,\%\!\?\$\:\(\)]', ' ', q)

def debug_file_distribution(stage: str, chunks: list[RetrievedChunk]):

    print("\n" + "=" * 80)
    print(f"📂 FILE DISTRIBUTION — {stage}")
    print("=" * 80)

    if not chunks:
        print("❌ No chunks")
        return

    counter = Counter()

    for c in chunks:
        counter[c.file_name] += 1

    print(f"Total chunks: {len(chunks)}")
    print()

    for file_name, count in counter.most_common():
        print(f"{count:3d} chunks -> {file_name}")

    print("\nTOP CHUNKS:\n")

    for i, c in enumerate(chunks[:30]):

        print(
            f"[{i:02d}] "
            f"file={c.file_name} | "
            f"page={c.page_number} | "
            f"chunk={c.chunk_id} | "
            f"rrf={c.rrf_score:.4f} | "
            f"rerank={c.rerank_score:.4f}"
        )

    print("=" * 80 + "\n")

def _get_adjacent_chunks(chunk):
    conn = Database.get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, document_id::text, file_name, page_number, chunk_id, content
                FROM embeddings
                WHERE document_id = %s AND page_number = %s AND chunk_id BETWEEN %s AND %s
                ORDER BY chunk_id
                """,
                (chunk.document_id, chunk.page_number, chunk.chunk_id - 1, chunk.chunk_id + 1)
            )
            rows = cur.fetchall()
            return [
                RetrievedChunk(id=r[0], document_id=r[1], file_name=r[2] or "", 
                               page_number=r[3] or 0, chunk_id=r[4], content=r[5] or "")
                for r in rows
            ]
    finally:
        Database.return_connection(conn)

def hybrid_search(
    query: str,
    top_k: int = DEFAULT_TOP_K*2,
    bm25_weight: float = DEFAULT_BM25_WEIGHT,
    hnsw_weight: float = DEFAULT_HNSW_WEIGHT,
) -> list[RetrievedChunk]:
    if not query or not query.strip():
        return []

    pool = top_k * 3
    normalized_query = normalize_query(query)

    with ThreadPoolExecutor(max_workers=2) as executor:
        bm25_f = executor.submit(_bm25_search, normalized_query, pool)
        hnsw_f = executor.submit(_hnsw_search, query, pool)
        bm25_results = bm25_f.result()
        hnsw_results = hnsw_f.result()

    fused = _reciprocal_rank_fusion(bm25_results, hnsw_results, bm25_weight, hnsw_weight)
    debug_file_distribution("RRF FUSED", fused)

    reranked = rerank(query, fused[:top_k * 3])
    debug_file_distribution("RERANKED", reranked)
    # logger.info("------ RERANKED RESULTS ------")
    # for i, r in enumerate(reranked):
    #     logger.info(f"[RERANK {i}] score={r.rerank_score} -> {r.content[:150]}")
    final_expanded = []
    top_chunks = reranked[:top_k*2]
    def get_support_chunks_for_chunk(chunk, candidates, max_support=2):
        # sort by similarity to current chunk (use rerank_score or embedding similarity)
        related = sorted(
            [c for c in candidates if c.id != chunk.id],
            key=lambda x: x.rerank_score,
            reverse=True
        )
        return related[:max_support]

    candidates = reranked[top_k:]   # remaining chunks

    for chunk in top_chunks:
        neighbors = _get_adjacent_chunks(chunk)

        support_chunks = get_support_chunks_for_chunk(chunk, candidates)

        prev_text = " ".join(c.content for c in neighbors if c.chunk_id < chunk.chunk_id)
        next_text = " ".join(c.content for c in neighbors if c.chunk_id > chunk.chunk_id)
        support_text = " ".join(c.content for c in support_chunks)

        sections = [f"Main content:\n{chunk.content}"]
        if prev_text.strip(): sections.append(f"Context before:\n{prev_text}")
        if next_text.strip(): sections.append(f"Context after:\n{next_text}")
        if support_text.strip(): sections.append(f"Supporting context:\n{support_text}")

        chunk.llm_content = "\n\n".join(sections)

        final_expanded.append(chunk)
    debug_file_distribution(
    "FINAL EXPANDED",
    final_expanded
    )
    return final_expanded