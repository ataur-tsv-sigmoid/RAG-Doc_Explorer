import os, re
from pdf2image import convert_from_path
from io import BytesIO
from PIL import Image
import nltk
from nltk.tokenize import sent_tokenize
from pypdf import PdfReader, PdfWriter
from langchain_core.documents import Document
from azure.identity import DefaultAzureCredential
from azure.ai.documentintelligence import DocumentIntelligenceClient
from concurrent.futures import ThreadPoolExecutor, as_completed

from app.services.storage_service import AzureStorageService
from app.services.db_service import insert_page, insert_ocr, get_ocr_pages
from app.services.embedding_service import generate_embedding
from app.services.db_service import insert_embedding
from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)



def split_pdf(file_path: str):
    reader = PdfReader(file_path, strict=False)
    page_files = []

    base_doc_name = os.path.splitext(os.path.basename(file_path))[0]

    base_doc_name = (
        base_doc_name
        .replace(" ", "_")
        .replace("'", "")
        .replace('"', "")
        .replace("-", "_")
    )

    for i, page in enumerate(reader.pages):
        writer = PdfWriter()
        writer.add_page(page)

        output_path = f"/tmp/{base_doc_name}_page_{i+1}.pdf"

        with open(output_path, "wb") as f:
            writer.write(f)

        page_files.append((output_path, i + 1))

    return page_files


def clean_text(text: str):
    text = text.lower()
    text = text.replace("<figure>", "").replace("</figure>", "")

    text = re.sub(r'[^a-z0-9\s\.\,\%\!\?\$\-\:\/]', ' ', text)

    return " ".join(text.split())

def safe_sent_tokenize(text):
    try:
        return sent_tokenize(text)
    except LookupError:
        # fallback (never crash)
        return re.split(r'(?<=[\.])\s+', text)

def _chunk_text(text, size=600):
    sentences = safe_sent_tokenize(text)
    chunks = []
    current = []
    current_len = 0
    overlap_len_target = int(size * 0.2)

    for sentence in sentences:
        if not sentence.strip():
            continue
        sentence_len = len(sentence)
        if current_len + sentence_len <= size:
            current.append(sentence)
            current_len += sentence_len
        else:
            chunk = " ".join(current).strip()
            if chunk:
                chunks.append(chunk)
            overlap = []
            temp_len = 0
            for s in reversed(current):
                if temp_len + len(s) > overlap_len_target:
                    break
                overlap.insert(0, s)
                temp_len += len(s)
            current = overlap + [sentence]
            current_len = sum(len(s) for s in current)

    if current:
        chunk = " ".join(current).strip()
        if chunk:
            chunks.append(chunk)
    return chunks

def table_to_structured_text(table):
    rows = {}

    # Build row-wise structure
    for cell in table.cells:
        r = cell.row_index
        c = cell.column_index

        if r not in rows:
            rows[r] = {}

        rows[r][c] = (cell.content or "").strip()

    if not rows:
        return []

    ordered_rows = [rows[r] for r in sorted(rows.keys())]

    # -----------------------------
    # ✅ Detect header row
    # -----------------------------
    def is_header(row):
        values = list(row.values())
        if not values:
            return False

        avg_len = sum(len(v) for v in values) / len(values)

        # Heuristic: headers are short
        return avg_len < 40   # 👈 threshold

    first_row = ordered_rows[0]
    col_indices = sorted(first_row.keys())

    structured = []
    structured.append("[TABLE]")

    if is_header(first_row):
        # ✅ Use detected header
        columns = []
        for c in col_indices:
            val = first_row.get(c, "").strip()
            columns.append(val if val else f"col_{c}")

        structured.append("columns: " + " | ".join(columns))
        data_rows = ordered_rows[1:]

    else:
        # ❌ No header → generate generic column names
        columns = [f"col_{c}" for c in col_indices]
        structured.append("columns: " + " | ".join(columns))
        data_rows = ordered_rows

    # -----------------------------
    # ✅ Process rows
    # -----------------------------
    for row in data_rows:
        structured.append("row:")
        for c, col_name in zip(col_indices, columns):
            value = row.get(c, "").strip()
            if value:
                structured.append(f"{col_name}: {value}")

    return structured
def run_ocr(file_path: str, document_id: str, doc_id: int, file_type: str):
    storage = AzureStorageService()
    all_docs = []

    client = DocumentIntelligenceClient(
        endpoint=settings.AZURE_DOC_INTEL_ENDPOINT,
        credential=DefaultAzureCredential()
    )
    
    reader = PdfReader(file_path)
    page_data = [(page, i + 1) for i, page in enumerate(reader.pages)]
    BATCH_SIZE = 4

    def process_page(page_tuple):
        pdf_path = None
        try:
            page, page_num = page_tuple
            logger.info(f"OCR on page {page_num}")

            safe_doc_name = (
            os.path.basename(document_id)
            .replace(" ", "_")
            .replace("/", "_")
            .replace("\\", "_")
        )

            pdf_path = f"/tmp/{safe_doc_name}_page_{page_num}.pdf"

            writer = PdfWriter()
            writer.add_page(page)

            with open(pdf_path, "wb") as f:
                writer.write(f)

            blob_path = storage.upload_page_pdf(
                local_path=pdf_path,
                document_name=document_id,
                page_num=page_num,
                file_type="pdf"
            )

            with open(pdf_path, "rb") as f:
                poller = client.begin_analyze_document(
                    "prebuilt-layout",
                    body=f
                )

            result = poller.result()
            logger.info(f"OCR completed for page {page_num}")

            insert_page(
                document_id=doc_id,
                page_number=page_num,
                page_blob_path=blob_path,
                local_path=pdf_path
            )

            extracted_parts = []
            # logger.info(f"{page_num} RESULT IS {result}")
            if result.tables:
                if result.paragraphs:
                    first_para = result.paragraphs[0].content.strip()
                    if first_para:
                        extracted_parts.append(f"[HEADING] {first_para}")

                for table in result.tables:
                    extracted_parts.extend(table_to_structured_text(table))

            if result.paragraphs:
                for para in result.paragraphs:
                    if para.content:
                        extracted_parts.append(para.content.strip())

            if result.pages:
                for page in result.pages:
                    for line in page.lines or []:
                        if line.content:
                            extracted_parts.append(line.content.strip())
            
            if not extracted_parts and result.pages:
                for page in result.pages:
                    for line in page.lines or []:
                        if line.content:
                            extracted_parts.append(line.content.strip())

            full_text = " ".join(extracted_parts)
            # logger.info(f"{page_num} CONTENT IS {full_text}")
            text = clean_text(full_text) if full_text else ""

            if not text:
                logger.warning(f"Empty OCR text for page {page_num}")

            # 🔥 chunk_type logic
            chunk_type = "body"

            if result.tables:
                chunk_type = "table"
            elif result.paragraphs:
                first_para = result.paragraphs[0].content.strip() if result.paragraphs else ""
                if first_para and len(first_para.split()) <= 6:
                    chunk_type = "title"

            tags = [chunk_type]

            # 🔥 STORE WITH chunk_type
            insert_ocr(
                document_id=doc_id,
                page_number=page_num,
                content=text,
                tags=tags,
                chunk_type=chunk_type
            )

            docs = [
                Document(
                    page_content=text,
                    metadata={
                        "document_id": document_id,
                        "source": document_id,
                        "page": page_num,
                        "chunk_type": chunk_type
                    }
                )
            ]

            return docs

        finally:
            if pdf_path and os.path.exists(pdf_path):
                os.remove(pdf_path)
    # -----------------------------
    # ✅ Parallel processing
    # -----------------------------

    for i in range(0, len(page_data), BATCH_SIZE):
        batch = page_data[i:i + BATCH_SIZE]

        logger.info(f"Processing batch: pages {[p[1] for p in batch]}")

        with ThreadPoolExecutor(max_workers=4) as executor:
            futures = [executor.submit(process_page, p) for p in batch]

            for future in as_completed(futures):
                try:
                    docs = future.result()
                    all_docs.extend(docs)
                except Exception as e:
                    logger.error(f"Error processing page: {e}")

    return all_docs

def process_batch(chunks, meta, doc_id, document_id, file_type):
    try:
        embeddings = generate_embedding(chunks, is_query=False)  # ⚠️ must support list input

        for emb, (page_num, chunk_id, chunk_type), chunk in zip(embeddings, meta, chunks):
            insert_embedding(
                id=f"{doc_id}_p{page_num}_c{chunk_id}",
                document_id=doc_id,
                file_name=document_id,
                file_type=file_type,
                page_number=page_num,
                chunk_id=chunk_id,
                content=chunk,
                embedding=emb,
                chunk_type=chunk_type
            )

    except Exception as e:
        logger.error(f"Batch embedding failed: {e}")

def run_embeddings(doc_id: int, document_id: str, file_type: str):
    logger.info("Starting batched embedding generation...")

    logger.info(f"Fetching OCR pages for doc_id={doc_id}")
    pages = get_ocr_pages(doc_id)
    logger.info(f"Pages fetched: {len(pages)}")

    BATCH_SIZE = 20  # 🔥 important

    batch_chunks = []
    batch_meta = []

    for page in pages:
        text = page["content"]
        page_num = page["page_number"]

        if not text.strip():
            continue

        chunks = _chunk_text(text)
        if not chunks:
            logger.warning(f"No chunks for page {page_num}, using full text")
            chunks = [text]

        for i, chunk in enumerate(chunks):
            if not chunk.strip():
                continue

            batch_chunks.append(chunk)
            chunk_type = "body"   # 🔥 simple for now
            batch_meta.append((page_num, i, chunk_type))

            # 🚀 When batch is full → process
            if len(batch_chunks) == BATCH_SIZE:
                process_batch(batch_chunks, batch_meta, doc_id, document_id, file_type)
                batch_chunks = []
                batch_meta = []

    # 🚀 process remaining
    if batch_chunks:
        process_batch(batch_chunks, batch_meta, doc_id, document_id, file_type)