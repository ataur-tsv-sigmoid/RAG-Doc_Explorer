import os
import logging

from concurrent.futures import ThreadPoolExecutor
from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from typing import Optional, List
from pypdf import PdfReader
from app.db.init_db import run_db_init
from app.services.llm_service import GroqLLMClient
_llm = GroqLLMClient()
from app.services.rag_service import rag_prepare
from app.services.rag_service import _history
from app.services.llm_service import ChatMessage

from app.db.db import Database
from app.core.config import settings
from app.core.logging import get_logger
from app.services.storage_service import AzureStorageService
from app.services.ingestion_azure_service import process_single_pdf
from app.services.retrieval_service import hybrid_search
from app.services.rag_service import rag_chat, clear_history
from app.services.db_service import create_document, update_status, update_page_count, get_all_documents, search_documents_by_title

logger = get_logger(__name__)

app = FastAPI(title="NeuralRAG")

# CORS: allows the React frontend on port 5173 to call this API
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)




from typing import List

class ChatRequest(BaseModel):
    message: str
    conversation_id: Optional[str] = "default_session"
    selected_pdf_ids: List[str] = []     # file_name basenames, e.g. ["report.pdf"]


class SearchRequest(BaseModel):
    query: str
    top_k: Optional[int] = 5
    search_type: Optional[str] = "content"   # ✅ ADD THIS


@app.on_event("startup")
def startup():
    Database.initialize()
    run_db_init()


@app.on_event("shutdown")
def shutdown():
    
    Database.close_all()


@app.get("/health")
def health():
    storage = AzureStorageService()
    return {"status": "ok"}

import json

@app.post("/chat/stream")
async def chat_stream(request: ChatRequest):

    def event_generator():
        full_answer = ""

        try:
            # ─────────────────────────────
            # 1. PREPARE RAG (NO LLM CALL)
            # ─────────────────────────────
            rag_data = rag_prepare(
                user_message=request.message,
                conversation_id=request.conversation_id,
                selected_pdf_ids=request.selected_pdf_ids,
            )

            system_prompt = rag_data["system_prompt"]
            history = rag_data["history"]
            chunks = rag_data["chunks"]

            # ─────────────────────────────
            # 2. STREAM TOKENS FROM LLM
            # ─────────────────────────────
            buffer = ""   # helps avoid flickering UI

            for token in _llm.generate_stream(
                system_prompt,
                history,
                request.message,
            ):
                full_answer += token
                buffer += token

                # flush every few chars (smooth streaming)
                if len(buffer) > 20:
                    yield f"data: {json.dumps({'type': 'token', 'content': buffer})}\n\n"
                    buffer = ""

            # flush remaining buffer
            if buffer:
                yield f"data: {json.dumps({'type': 'token', 'content': buffer})}\n\n"

            # ─────────────────────────────
            # 3. STORE CONVERSATION MEMORY
            # ─────────────────────────────
            _history.append(
                request.conversation_id,
                ChatMessage("user", request.message)
            )

            _history.append(
                request.conversation_id,
                ChatMessage("assistant", full_answer)
            )

            # ─────────────────────────────
            # 4. FINAL EVENT (SOURCES)
            payload = {
                "type": "done",
                "sources": [
                    {
                        "file_name": c.file_name,
                        "page_number": c.page_number,
                    }
                    for c in chunks
                ],
            }

            yield f"data: {json.dumps(payload)}\n\n"

        except Exception as e:
            # ─────────────────────────────
            # ERROR HANDLING
            # ─────────────────────────────
            error_payload = {
            "type": "error",
            "content": str(e),
            }

            yield f"data: {json.dumps(error_payload)}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        },
    )

def process_file(file, storage):
    temp_path = f"/tmp/{file.filename}"
    doc_id = None

    try:
        # Save file
        with open(temp_path, "wb") as f:
            f.write(file.file.read())

        # Upload
        blob_name = storage.upload_file(temp_path, file_type="pdf")

        # DB
        doc_id, already_exists = create_document(
            file_name=file.filename,
            blob_path=blob_name,
            container=settings.AZURE_STORAGE_CONTAINER,
            file_type="pdf",
        )

        if already_exists:
            return {"file": file.filename, "status": "already_processed"}

        update_status(doc_id, "processing")

        # 🔥 Full pipeline
        docs = process_single_pdf(temp_path, file.filename, doc_id, "pdf")

        page_count = len(PdfReader(temp_path).pages)

        update_page_count(doc_id, page_count)
        update_status(doc_id, "completed")

        return {
            "file": file.filename,
            "status": "completed",
            "pages": page_count,
            "chunks": len(docs),
        }

    except Exception as e:
        if doc_id:
            update_status(doc_id, "failed")

        return {
            "file": file.filename,
            "status": "failed",
            "error": str(e),
        }

    finally:
        if os.path.exists(temp_path):
            os.remove(temp_path)

@app.post("/upload-multiple")
async def upload_multiple(files: List[UploadFile] = File(...)):
    storage = AzureStorageService()

    with ThreadPoolExecutor(max_workers=5) as executor:
        results = list(executor.map(lambda f: process_file(f, storage), files))

    return {"results": results}

@app.post("/upload")
async def upload_pdf(file: UploadFile = File(...)):
    storage = AzureStorageService()
    temp_path = f"/tmp/{file.filename}"
    doc_id = None  # must be set before try so except can always reference it

    MAX_FILE_SIZE_MB = 50
    MAX_FILE_SIZE_BYTES = MAX_FILE_SIZE_MB * 1024 * 1024
    file_size = 0

    with open(temp_path, "wb") as f:
        while True:
            chunk = await file.read(1024 * 1024)  # 1MB chunks
            if not chunk:
                break

            file_size += len(chunk)

            if file_size > MAX_FILE_SIZE_BYTES:
                f.close()
                if os.path.exists(temp_path):
                    os.remove(temp_path)

                raise HTTPException(
                    status_code=413,
                    detail=f"File too large. Max allowed size is {MAX_FILE_SIZE_MB} MB"
                )

            f.write(chunk)

    try:
        ext = file.filename.rsplit(".", 1)[-1].lower()
        if ext == "pdf":
            file_type = "pdf"
        elif ext in ("png", "jpg", "jpeg"):
            file_type = "image"
        elif ext == "csv":
            file_type = "csv"
        else:
            raise HTTPException(
                status_code=400,
                detail=f"Unsupported file type: .{ext}. Accepted: pdf, png, jpg, jpeg, csv",
            )

        blob_name = storage.upload_file(temp_path, file_type=file_type)
        clean_name = file.filename
        doc_id, already_exists = create_document(
            file_name=clean_name,
            blob_path=blob_name,
            container=settings.AZURE_STORAGE_CONTAINER,
            file_type=file_type,
        )

        if already_exists:
            return {"message": "Document already processed", "document_id": str(doc_id)}

        update_status(doc_id, "processing")
        docs = process_single_pdf(temp_path, file.filename, doc_id, file_type)

        page_count = 0
        if file_type == "pdf":
            page_count = len(PdfReader(temp_path).pages)

        update_page_count(doc_id, page_count)
        update_status(doc_id, "completed")

        return {
            "message": "uploaded + processed",
            "document_id": str(doc_id),
            "file": blob_name,
            "pages": page_count,
            "chunks": len(docs),
        }

    except HTTPException:
        raise

    except Exception as exc:
        logger.error(f"upload_pdf failed: {exc}", exc_info=True)
        if doc_id is not None:
            try:
                update_status(doc_id, "failed")
            except Exception:
                pass
        raise HTTPException(status_code=500, detail=str(exc))

    finally:
        if os.path.exists(temp_path):
            os.remove(temp_path)


@app.get("/pdfs")
def list_pdfs():
    try:
        docs = get_all_documents()

        return {
            "results": docs
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.post("/process/{file_name:path}")
def process_pdf(file_name: str):
    storage = AzureStorageService()
    local_path = f"/tmp/{os.path.basename(file_name)}"
    storage.download_file(file_name, local_path)

    doc_id, already_exists = create_document(
        file_name=file_name,
        blob_path=file_name,
        container=settings.AZURE_STORAGE_CONTAINER,
        file_type="pdf",
    )

    if already_exists:
        return {"message": "Document already processed", "document_id": str(doc_id)}

    update_status(doc_id, "processing")
    try:
        docs = process_single_pdf(local_path, file_name, doc_id, "pdf")
        page_count = len(PdfReader(local_path).pages)
        update_page_count(doc_id, page_count)
        update_status(doc_id, "completed")
    except Exception as exc:
        update_status(doc_id, "failed")
        raise HTTPException(status_code=500, detail=str(exc))
    finally:
        if os.path.exists(local_path):
            os.remove(local_path)

    return {"message": "processed", "pages": page_count}


@app.get("/download/{blob_path:path}")
def download_pdf(blob_path: str):
    storage = AzureStorageService()
    logger.info(f"RAW incoming path: {blob_path}    "*20)
    # blobs = storage.container_client.list_blobs(name_starts_with="pdfs/raw/")
    # for b in blobs:
    #     print("BLOBS"*2,repr(b.name))
    blob_client = storage.container_client.get_blob_client(blob_path)

    logger.info(f"FINAL blob path used: {blob_path}"     *20)
    print("BLOB"*100, blob_path)
    blob_client = storage.container_client.get_blob_client(blob_path)
    stream = blob_client.download_blob()
    props = blob_client.get_blob_properties()
    return StreamingResponse(
        stream.chunks(),
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{blob_path.split("/")[-1]}"',
            "Content-Length": str(props.size),
        },
    )


# NEW: Download the full raw PDF for a given document filename (basename only, e.g. "report.pdf")
@app.get("/download-full/{file_name:path}")
def download_full_pdf(file_name: str):
    """
    Download the complete raw PDF from pdfs/raw/<file_name>.
    Accepts the basename (e.g. 'report.pdf') or the full blob path.
    """
    storage = AzureStorageService()
    # Normalise: if caller passes just a basename, prefix with pdfs/raw/
    if not file_name.startswith("pdfs/"):
        blob_path = f"pdfs/raw/{os.path.basename(file_name)}"
    else:
        blob_path = file_name
    try:
        blob_client = storage.container_client.get_blob_client(blob_path)
        stream = blob_client.download_blob()
        props = blob_client.get_blob_properties()
        return StreamingResponse(
            stream.chunks(),
            media_type="application/pdf",
            headers={
                "Content-Disposition": f'attachment; filename="{blob_path.split("/")[-1]}"',
                "Content-Length": str(props.size),
            },
        )
    except Exception as exc:
        logger.error(f"download_full_pdf failed for '{blob_path}': {exc}", exc_info=True)
        raise HTTPException(status_code=404, detail=f"File not found: {blob_path}")


# NEW: Download a single page PDF for a given document.
# Query params: document_name (raw PDF name) and page_number (1-based int)
@app.get("/download-page/{document_name:path}")
def download_page_pdf(document_name: str, page_number: int):
    storage = AzureStorageService()

    base_name = os.path.splitext(os.path.basename(document_name))[0]
    prefix = f"pdfs/{base_name}/"

    blobs = storage.container_client.list_blobs(name_starts_with=prefix)

    target_blob = None
    for b in blobs:
        if f"_page_{page_number}.pdf" in b.name:
            target_blob = b.name
            break

    if not target_blob:
        raise HTTPException(status_code=404, detail="Page not found")

    blob_client = storage.container_client.get_blob_client(target_blob)
    stream = blob_client.download_blob()
    props = blob_client.get_blob_properties()

    return StreamingResponse(
        stream.chunks(),
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{target_blob.split("/")[-1]}"',
            "Content-Length": str(props.size),
        },
    )


# NEW: List all page-wise PDFs available for a document
@app.get("/pages/{document_name:path}")
def list_document_pages(document_name: str):
    """
    Returns the list of page blob paths for the given document.
    Used by the frontend to populate per-page download options.
    """
    storage = AzureStorageService()
    try:
        pages = storage.list_pages_for_document(document_name)
        return {"document": document_name, "pages": pages}
    except Exception as exc:
        logger.error(f"list_document_pages failed: {exc}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc))


@app.post("/search")
async def search_documents(request: SearchRequest):
    try:
        storage = AzureStorageService()

        # =========================
        # ✅ TITLE SEARCH (RAW PDFs ONLY)
        # =========================
        if request.search_type == "title":
            results = search_documents_by_title(
                query=request.query,
                top_k=request.top_k
            )

            return {
                "results": results
            }

        # =========================
        # ✅ CONTENT SEARCH (UNCHANGED)
        # =========================
        results = hybrid_search(query=request.query, top_k=5)
        # ✅ Apply HNSW score threshold filtering
        filtered_results = [
            r for r in results
            if (r.hnsw_score is None) or (r.hnsw_score > 0.8)
        ]
        if not filtered_results:
            return {
                "results": [],
                "message": "No results found"
            }
        
        return {
            "results": [
                {
                    "id": r.id,
                    "document_id": r.document_id,
                    "file_name": r.file_name,
                    "page_number": r.page_number,
                    "chunk_id": r.chunk_id,

                    "bm25_rank": r.bm25_rank,
                    "hnsw_rank": r.hnsw_rank,

                    "bm25_score": r.bm25_score,
                    "hnsw_score": r.hnsw_score,

                    "content": r.content,
                    "rrf_score": r.rrf_score,
                }
                for r in filtered_results
            ]
        }

    except Exception as exc:
        logger.error(f"search failed: {exc}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc))


@app.post("/chat")
async def chat_with_docs(request: ChatRequest):
    try:
        response = rag_chat(
            user_message=request.message,
            conversation_id=request.conversation_id,
            selected_pdf_ids=request.selected_pdf_ids,   # NEW
        )

        return {
            "answer": response.answer,
            "conversation_id": response.conversation_id,
            "sources": response.metadata["chunks"],  # ✅ updated
            # NEW: simplified source references with just file_name and page_number
            "source_references": [
                {
                    "file_name": chunk["file_name"],
                    "page_number": chunk["page_number"],
                }
                for chunk in response.metadata["chunks"]
            ],
        }

    except Exception as exc:
        logger.error(f"chat failed: {exc}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc))

@app.delete("/chat/history/{conversation_id}")
async def reset_chat(conversation_id: str):
    storage = AzureStorageService()
    clear_history(conversation_id)
    return {"message": f"History for '{conversation_id}' cleared."}
