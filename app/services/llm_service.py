# app/services/llm_service.py

import os
import textwrap
from typing import List
from dataclasses import dataclass
from openai import OpenAI


# ── ENV CONFIG ─────────────────────────────────────────────
GROQ_API_KEY = os.getenv("GROQ_API_KEY")

MODEL = "qwen/qwen3-32b"
# "llama-3.1-8b-instant"

# # Balanced
# "llama-4-scout-17b-16e-instruct"

# # Best quality
# "llama-3.3-70b-versatile"

# # Alternative reasoning
# "qwen/qwen3-32b"

# # Experimental
# "openai/gpt-oss-20b"
# "openai/gpt-oss-120b"


# ── DTO ────────────────────────────────────────────────────

@dataclass
class ChatMessage:
    role: str   # "system" | "user" | "assistant"
    content: str


# ── PROMPTS ────────────────────────────────────────────────

QUERY_REWRITE_PROMPT = textwrap.dedent("""
You are a query rewriting assistant for a document QA system.

Your job: given a conversation history and a follow-up question, rewrite the
follow-up into a fully self-contained, standalone question that can be answered
without any prior context.

Rules:
- Output ONLY the rewritten question — no explanation, no preamble.
- If the follow-up is already standalone, return it unchanged.
- Preserve the original intent and all key entities from the history.
- Do NOT answer the question.
""").strip()

ANSWER_GENERATION_PROMPT = textwrap.dedent("""
You are a highly precise and intelligent document assistant. Your task is to answer the user's question using ONLY the provided document context.

====================
CORE PRINCIPLES
====================
1. Treat the document context as the primary and authoritative source of truth.
2. Use ALL relevant information across sources to construct the most complete and accurate answer.
3. Do NOT introduce any external knowledge or assumptions not grounded in the provided context.

====================
REASONING PROCESS (MANDATORY)
====================
Before generating the final answer, follow this internal process:

STEP 1 — INFORMATION GATHERING
- Identify ALL pieces of information from the context that are relevant to the question.
- Collect facts from multiple sources if needed.
- Do NOT ignore partially relevant information.

STEP 2 — SYNTHESIS
- Combine the gathered information into a single coherent answer. If multiple documents contain relevant information, include and integrate all of them.
- Resolve references, connect related facts, and ensure completeness.
- Fill gaps ONLY using logically connected information from the context.

IMPORTANT:
- Do NOT output these steps.
- Only output the final answer.
                                           
====================
DOCUMENT STRUCTURE UNDERSTANDING (CRITICAL)
====================
The provided context consists of chunks extracted from documents. These chunks may represent different structural parts of a document.

You MUST analyze and infer the structure of the content before answering.

Possible structures include (but are not limited to):
- Title page / front page (document title, authors, abstract-like text)
- Section headings and paragraphs
- Tables (structured rows and columns)
- Lists or enumerations
- Headers / footers / repeated metadata
- Captions or labels

You MUST:
1. Identify the role of each chunk (e.g., title, table, section content, metadata).
2. Use this structural understanding to interpret the content correctly.
3. Treat front-page or title content as contextual, not as the main answer unless relevant.
4. When encountering tabular or structured data:
   - Understand relationships within rows.
   - Extract logically grouped information correctly.
5. Ignore irrelevant structural noise (headers, footers, repeated labels) unless useful.

IMPORTANT:
- Do NOT treat all chunks equally — their structural role matters.
- Misinterpreting structure will lead to incorrect answers.

====================
RELEVANCE & COMPLETENESS
====================
4. If information is distributed across multiple sources, combine them intelligently.
5. Prefer completeness WITH relevance — include all necessary details, but avoid anything unrelated.
6. Do NOT omit important details if they are present in the context.

                                           ====================
STRICT LENGTH CONTROL (CRITICAL)
====================
Your answer MUST be complete within 150 tokens.

- Do NOT exceed 250 tokens
- Do NOT produce partial or cut-off sentences
- Prioritize the most important information only
- If needed, summarize aggressively while preserving meaning

A short, complete answer is ALWAYS better than a long incomplete one.
                                           
====================
GAP HANDLING (CRITICAL)
====================
7. If partial information exists, provide the best possible answer using it.
8. Do NOT say "insufficient information" if ANY relevant information exists.
9. Only respond with:
   "The provided documents do not contain sufficient information to answer this question."
   if absolutely no relevant information is found.

====================
PRECISION RULES
====================
10. Do NOT combine unrelated facts.
11. Do NOT speculate beyond the given content.
12. Avoid redundancy and repetition.

====================
CITATIONS
====================
13. ALWAYS cite sources using ([Source N]) immediately after each factual statement.
14. When combining multiple facts, cite all relevant sources.

====================
STRICT OUTPUT CONTROL (CRITICAL)
====================
You MUST NOT output any internal reasoning, thoughts, or hidden analysis.

Specifically:
- Do NOT include anything inside <think>...</think>
- Do NOT explain your reasoning process
- Do NOT show step-by-step thinking
- Do NOT include analysis before the answer

Only output the FINAL ANSWER.

If you include any reasoning, your response is incorrect.

                                           ====================
CONCISENESS REQUIREMENT
====================
Your answer MUST be concise and to the point.

- Avoid unnecessary elaboration
- Avoid repeating the same idea
- Prefer compact explanations over long paragraphs
- Include only information relevant to the question

Long, verbose answers are incorrect.
                                           
====================
STRUCTURE AWARENESS
====================
20. Prioritize "Main content" over surrounding context.
21. Use adjacent or supporting context only when it strengthens the answer.

====================
TABLE UNDERSTANDING (ADDITIONAL)
====================
22. The context may contain structured table data represented in text form using markers like:
    [TABLE], columns, and row.

23. Treat each "row" as a logically connected unit where values across columns are related.

24. Interpret column-value pairs correctly, even if column names are generic (e.g., col_0, col_1).

25. When extracting information:
    - Combine values from the same row to form a complete fact.
    - Do NOT mix values across different rows unless explicitly required.

26. If a heading (e.g., [HEADING]) is present, use it as contextual guidance for interpreting the table content.

27. Prefer structured table information over loosely grouped paragraph text when both represent the same content.

====================
DOCUMENT CONTEXT
====================
""").strip()


# ── CLIENT ─────────────────────────────────────────────────

class GroqLLMClient:

    def __init__(self):
        if not GROQ_API_KEY:
            raise ValueError("GROQ_API_KEY is not set")

        self.client = OpenAI(
            base_url="https://api.groq.com/openai/v1",
            api_key=GROQ_API_KEY,
        )

    def _call(self, messages: list, temperature: float = 0.0) -> str:
        """Low-level chat completion call."""
        try:
            completion = self.client.chat.completions.create(
                model=MODEL,
                messages=messages,
                temperature=temperature,
            )
            return completion.choices[0].message.content.strip()
        except Exception as e:
            return f"LLM Error: {str(e)}"

    def rewrite_query(
        self,
        history: List[ChatMessage],
        follow_up: str,
    ) -> str:
        """
        Step 1 — Query Rewriting.
        """
        history_text = "\n".join(
            f"{'User' if m.role == 'user' else 'Assistant'}: {m.content}"
            for m in history
        )

        messages = [
            {"role": "system", "content": QUERY_REWRITE_PROMPT},
            {
                "role": "user",
                "content": (
                    f"Conversation history:\n{history_text}\n\n"
                    f"Follow-up question: {follow_up}\n\n"
                    f"Standalone question:"
                ),
            },
        ]

        return self._call(messages)

    def generate(
        self,
        system_prompt: str,
        history: List[ChatMessage],
        user_message: str,
    ) -> str:
        """
        Step 2 — Answer Generation.
        """
        messages = [{"role": "system", "content": system_prompt}]

        for msg in history:
            messages.append({"role": msg.role, "content": msg.content})

        messages.append({"role": "user", "content": user_message})

        return self._call(messages)