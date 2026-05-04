from app.db.db import Database

def search_documents_by_title(query: str, top_k: int = 5):
    conn = Database.get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT document_id, file_name, page_count
                FROM documents
                WHERE status = 'completed'
                  AND LOWER(file_name) LIKE %s
                ORDER BY created_at DESC
                LIMIT %s
            """, (f"%{query.lower()}%", top_k))

            rows = cur.fetchall()

            return [
                {
                    "document_id": str(r[0]),
                    "file_name": r[1],
                    "page_number": None,
                    "chunk_id": None,
                    "content": f"Document: {r[1]}",
                    "rrf_score": 1.0
                }
                for r in rows
            ]
    finally:
        Database.return_connection(conn)
        
def get_all_documents():
    conn = Database.get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT document_id, file_name, blob_path, status, page_count
                FROM documents
                WHERE status = 'completed'
                ORDER BY created_at DESC
            """)

            rows = cur.fetchall()

            return [
                {
                    "document_id": str(r[0]),
                    "file_name": r[1],
                    "blob_path": r[2],
                    "status": r[3],
                    "page_count": r[4],
                }
                for r in rows
            ]
    finally:
        Database.return_connection(conn)

def create_document(file_name, blob_path, container, file_type):
    conn = Database.get_connection()
    try:
        with conn:
            with conn.cursor() as cur:

                cur.execute(
                    "SELECT document_id, status FROM documents WHERE file_name=%s",
                    (file_name,)
                )
                result = cur.fetchone()

                if result:
                    doc_id, status = result

                    if status == "completed":
                        return doc_id, True

                    cur.execute(
                        """
                        UPDATE documents
                        SET blob_path=%s,
                            storage_container=%s,
                            file_type=%s,
                            updated_at=NOW(),
                            status=%s
                        WHERE document_id=%s
                        """,
                        (blob_path, container, file_type, "pending", doc_id)
                    )

                else:
                    cur.execute(
                        """
                        INSERT INTO documents (
                            file_name,
                            blob_path,
                            storage_container,
                            file_type,
                            status
                        )
                        VALUES (%s, %s, %s, %s, %s)
                        RETURNING document_id
                        """,
                        (file_name, blob_path, container, file_type, "pending")
                    )

                    doc_id = cur.fetchone()[0]

                return doc_id, False

    finally:
        Database.return_connection(conn)


def update_status(doc_id, status):
    conn = Database.get_connection()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE documents SET status=%s WHERE document_id=%s",
                    (status, doc_id)
                )
    finally:
        Database.return_connection(conn)


def insert_page(document_id, page_number, page_blob_path, local_path):
    conn = Database.get_connection()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO pages (
                        document_id,
                        page_number,
                        page_blob_path,
                        local_path
                    )
                    VALUES (%s, %s, %s, %s)
                    ON CONFLICT (document_id, page_number) DO NOTHING
                    """,
                    (document_id, page_number, page_blob_path, local_path)
                )
    finally:
        Database.return_connection(conn)


def insert_ocr(document_id, page_number, content, tags, chunk_type):
    conn = Database.get_connection()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO ocr_results (
                        document_id,
                        page_number,
                        content,
                        tags,
                        chunk_type
                    )
                    VALUES (%s, %s, %s, %s, %s)
                    ON CONFLICT (document_id, page_number) DO NOTHING
                    """,
                    (document_id, page_number, content, tags, chunk_type)
                )
    finally:
        Database.return_connection(conn)


def update_page_count(doc_id, page_count):
    conn = Database.get_connection()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE documents SET page_count=%s WHERE document_id=%s",
                    (page_count, doc_id)
                )
    finally:
        Database.return_connection(conn)


def insert_embedding(
    id,
    document_id,
    file_name,
    file_type,
    page_number,
    chunk_id,
    content,
    embedding,
    chunk_type
):
    conn = Database.get_connection()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO embeddings (
                        id, document_id, file_name, file_type,
                        page_number, chunk_id, content, embedding, chunk_type
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (document_id, page_number, chunk_id) DO NOTHING
                    """,
                    (
                        id,
                        document_id,
                        file_name,
                        file_type,
                        page_number,
                        chunk_id,
                        content,
                        embedding,
                        chunk_type
                    )
                )
    finally:
        Database.return_connection(conn)

def get_ocr_pages(document_id: int):
    conn = Database.get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT page_number, content
                FROM ocr_results
                WHERE document_id = %s
                ORDER BY page_number
            """, (document_id,))
            
            rows = cur.fetchall()

            return [
                {
                    "page_number": r[0],
                    "content": r[1]
                }
                for r in rows
            ]
    finally:
        Database.return_connection(conn)