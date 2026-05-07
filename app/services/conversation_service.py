import numpy as np
from typing import List

from app.db.db import Database
from app.services.embedding_service import generate_embedding
from app.services.llm_service import ChatMessage


MAX_HISTORY_TURNS = 6
SEMANTIC_TOP_K = 6


class ConversationService:

    @staticmethod
    def _get_next_turn_index(conversation_id: str) -> int:
        conn = Database.get_connection()

        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT COALESCE(MAX(turn_index), -1) + 1
                    FROM conversations
                    WHERE conversation_id = %s
                    """,
                    (conversation_id,)
                )

                return cur.fetchone()[0]

        finally:
            Database.return_connection(conn)

    @staticmethod
    def append(
        conversation_id: str,
        msg: ChatMessage,
    ):
        conn = Database.get_connection()

        try:
            turn_index = ConversationService._get_next_turn_index(
                conversation_id
            )

            if msg.role == "user":
                embedding = generate_embedding(
                    msg.content,
                    is_query=True
                )
            else:
                embedding = generate_embedding(
                    msg.content,
                    is_query=False
                )

            with conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        INSERT INTO conversations (
                            conversation_id,
                            turn_index,
                            role,
                            content,
                            embedding
                        )
                        VALUES (%s, %s, %s, %s, %s)
                        """,
                        (
                            conversation_id,
                            turn_index,
                            msg.role,
                            msg.content,
                            embedding
                        )
                    )

        finally:
            Database.return_connection(conn)

    @staticmethod
    def get_recent(
        conversation_id: str,
        n: int = MAX_HISTORY_TURNS,
    ) -> List[ChatMessage]:

        conn = Database.get_connection()

        try:
            with conn.cursor() as cur:

                limit_count = n * 2

                cur.execute(
                    """
                    SELECT role, content
                    FROM conversations
                    WHERE conversation_id = %s
                    ORDER BY turn_index DESC
                    LIMIT %s
                    """,
                    (conversation_id, limit_count)
                )

                rows = cur.fetchall()

                rows.reverse()

                return [
                    ChatMessage(
                        role=r[0],
                        content=r[1]
                    )
                    for r in rows
                ]

        finally:
            Database.return_connection(conn)

    @staticmethod
    def semantic_search(
        conversation_id: str,
        query: str,
        top_k: int = SEMANTIC_TOP_K,
    ) -> List[ChatMessage]:

        query_embedding = generate_embedding(
            query,
            is_query=True
        )

        if query_embedding is None:
            return ConversationService.get_recent(
                conversation_id
            )

        conn = Database.get_connection()

        try:
            with conn.cursor() as cur:

                cur.execute(
                    """
                    SELECT
                        turn_index,
                        role,
                        content
                    FROM conversations
                    WHERE conversation_id = %s
                    ORDER BY embedding <=> %s::vector
                    LIMIT %s
                    """,
                    (
                        conversation_id,
                        query_embedding,
                        top_k
                    )
                )

                rows = cur.fetchall()

                rows.sort(key=lambda x: x[0])

                messages = []

                for row in rows:
                    messages.append(
                        ChatMessage(
                            role=row[1],
                            content=row[2]
                        )
                    )

                return messages

        finally:
            Database.return_connection(conn)

    @staticmethod
    def clear(conversation_id: str):

        conn = Database.get_connection()

        try:
            with conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        DELETE FROM conversations
                        WHERE conversation_id = %s
                        """,
                        (conversation_id,)
                    )

        finally:
            Database.return_connection(conn)

    @staticmethod
    def cleanup_old_conversations(days: int = 7):

        conn = Database.get_connection()

        try:
            with conn:
                with conn.cursor() as cur:

                    cur.execute(
                        f"""
                        DELETE FROM conversations
                        WHERE created_at <
                        NOW() - INTERVAL '{days} days'
                        """
                    )

                    deleted = cur.rowcount

                    print(
                        f"🧹 Deleted {deleted} old conversation rows"
                    )

        finally:
            Database.return_connection(conn)