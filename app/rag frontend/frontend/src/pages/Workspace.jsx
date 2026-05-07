import { useState, useRef, useEffect } from "react";
import { useSearchParams, useNavigate } from "react-router-dom";
import { Document, Page, pdfjs } from "react-pdf";
import Markdown from "react-markdown";
import { Send, Maximize2, Minimize2, ChevronLeft, ChevronRight, Loader2, Search, FileText, Plus, Trash2, History, PanelLeftClose, PanelLeftOpen, Download } from "lucide-react";
import "react-pdf/dist/Page/AnnotationLayer.css";
import "react-pdf/dist/Page/TextLayer.css";
import { motion, AnimatePresence } from "framer-motion";


// Configure PDF worker
pdfjs.GlobalWorkerOptions.workerSrc = new URL(
  'pdfjs-dist/build/pdf.worker.min.mjs',
  import.meta.url,
).toString();

export default function Workspace() {
  const generateChatId = () => {
    return `chat_${Date.now()}_${Math.floor(Math.random() * 100000)}`;
  };
  const [searchParams] = useSearchParams();
  const docsParam = searchParams.get("docs");
  const singleDocParam = searchParams.get("doc");

  const handleClearHistory = async () => {

    try {

      await fetch(
        `/chat/history/${conversationId}`,
        {
          method: "DELETE",
        }
      );

      setMessages([]);

    } catch (err) {

      console.error("Failed to clear history", err);

    }
  };

  let docs = [];
  if (docsParam) {
    docs = docsParam.split(',').filter(Boolean);
  } else if (singleDocParam) {
    docs = [singleDocParam];
  }

  const chatIdParam = searchParams.get("chat_id");

  const [query, setQuery] = useState("");
  const [conversationId, setConversationId] = useState(() => {
    return chatIdParam || generateChatId();
  });
  const [messages, setMessages] = useState([]);
  const [loading, setLoading] = useState(false);
  const [jumpTarget, setJumpTarget] = useState(null);
  const [isExpanded, setIsExpanded] = useState(false);
  const navigate = useNavigate();
  const endRef = useRef(null);
  useEffect(() => {

    const params = new URLSearchParams(searchParams);

    params.set("chat_id", conversationId);

    navigate(
      `/workspace?${params.toString()}`,
      { replace: true }
    );

  }, [conversationId]);
  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, loading]);

  const handleAsk = async () => {
    if (!query.trim()) return;

    const userMsg = { role: "user", text: query };
    setMessages((prev) => [...prev, userMsg]);
    setLoading(true);
    setQuery("");

    let selectedFileNames = docs.map(d => d.split('/').pop());

    const res = await fetch("/chat/stream", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        message: userMsg.text,
        conversation_id: conversationId,
        selected_pdf_ids: selectedFileNames,
      }),
    });

    const reader = res.body.getReader();
    const decoder = new TextDecoder();

    let botMessage = "";

    // create empty bot message
    setMessages(prev => [...prev, { role: "bot", text: "" }]);

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      const chunk = decoder.decode(value);
      const lines = chunk.split("\n\n");

      for (let line of lines) {
        if (!line.startsWith("data:")) continue;

        const data = JSON.parse(line.replace("data: ", ""));

        if (data.type === "token") {
          botMessage += data.content;

          setMessages(prev => {
            const updated = [...prev];
            updated[updated.length - 1].text = botMessage;
            return updated;
          });
        }

        if (data.type === "done") {
          setMessages(prev => {
            const updated = [...prev];
            updated[updated.length - 1].sources = data.sources;
            return updated;
          });
        }
      }
    }

    setLoading(false);
  };

  const onKeyDown = (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleAsk();
    }
  };

  return (
    <div style={{ display: 'flex', height: '100%', overflow: 'hidden' }}>

      {/* Middle side: PDF Viewer */}
      <div style={{
        flex: isExpanded ? 1 : 1.2,
        borderRight: '1px solid var(--border)',
        display: 'flex',
        flexDirection: 'column',
        backgroundColor: '#1f2229',
        transition: 'flex 0.3s ease'
      }}>
        <div style={{ padding: '1rem', borderBottom: '1px solid var(--border)', display: 'flex', justifyContent: 'space-between', alignItems: 'center', backgroundColor: 'var(--background)' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem' }}>
            <FileText size={20} color="var(--primary)" />
            <span style={{ fontWeight: 600 }}>Document Preview ({docs.length})</span>
          </div>
          <button className="btn btn-ghost" onClick={() => setIsExpanded(!isExpanded)} title="Toggle layout">
            {isExpanded ? <Minimize2 size={18} /> : <Maximize2 size={18} />}
          </button>
        </div>

        <div className="custom-scrollbar" style={{ flex: 1, overflow: 'auto', display: 'flex', flexDirection: 'column', alignItems: 'center', padding: '2rem' }}>
          {docs.length > 0 ? (
            docs.map((doc, idx) => (
              <PdfViewer key={idx} docParam={doc} jumpTarget={jumpTarget} isExpanded={isExpanded} />
            ))
          ) : (
            <div style={{ boxShadow: 'var(--shadow-lg)', backgroundColor: 'white', minHeight: '800px', minWidth: '600px', display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', color: '#666' }}>
              <FileText size={64} style={{ opacity: 0.2, marginBottom: '1rem' }} />
              <p>No document selected</p>
              <p style={{ fontSize: '0.875rem' }}>Select documents from the library to preview</p>
            </div>
          )}
        </div>
      </div>

      {/* Right side: Chat */}
      <div style={{ flex: isExpanded ? 3 : 2.5, display: 'flex', flexDirection: 'column', backgroundColor: 'var(--background)', transition: 'flex 0.3s ease' }}>
        <div style={{ padding: '1rem', borderBottom: '1px solid var(--border)', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <div
            style={{
              display: "flex",
              alignItems: "center",
              justifyContent: "space-between",
              marginBottom: "1rem",
              position: "relative",
              zIndex: 10,
              pointerEvents: "auto",
            }}
          >
            <h2
              style={{
                fontSize: "1.25rem",
                marginBottom: "0.25rem",
              }}
            >
              AI Workspace
            </h2>

            <div
              style={{
                display: "flex",
                gap: "0.5rem",
                pointerEvents: "auto",
              }}
            >
              {/* NEW CHAT */}
              <button
                type="button"
                onClick={() => {
                  const newChatId = generateChatId();

                  setConversationId(newChatId);

                  setMessages([]);

                  const params = new URLSearchParams(searchParams);

                  params.set("chat_id", newChatId);

                  navigate(`/workspace?${params.toString()}`);
                }}
                style={{
                  cursor: "pointer",
                  padding: "0.45rem 0.8rem",
                  borderRadius: "8px",
                  border: "1px solid #ccc",
                  background: "#fff",
                }}
              >
                New Chat
              </button>

              {/* CLEAR HISTORY */}
              <button
                type="button"
                onClick={handleClearHistory}
                style={{
                  cursor: "pointer",
                  padding: "0.45rem 0.8rem",
                  borderRadius: "8px",
                  border: "1px solid #ffb3b3",
                  background: "#ffe5e5",
                  color: "#c62828",
                  position: "relative",
                  zIndex: 20,
                  pointerEvents: "auto",
                }}
              >
                Clear History
              </button>
            </div>
          </div>
        </div>

        <div className="custom-scrollbar" style={{ flex: 1, overflowY: 'auto', padding: '1.5rem', display: 'flex', flexDirection: 'column', gap: '1.5rem' }}>
          {messages.length === 0 && (
            <div style={{ margin: 'auto', textAlign: 'center', color: 'var(--muted-foreground)' }}>
              <div style={{ background: 'var(--muted)', display: 'inline-block', padding: '1rem', borderRadius: '50%', marginBottom: '1rem' }}>
                <Search size={32} color="var(--primary)" />
              </div>
              <h3 style={{ fontSize: '1.2rem', fontWeight: 600, color: 'var(--foreground)' }}>Ask anything</h3>
              <p style={{ marginTop: '0.5rem', maxWidth: '300px' }}>Ask questions, extract information, or summarize the document.</p>
            </div>
          )}

          {messages.map((m, i) => (
            <motion.div
              initial={{ opacity: 0, y: 10 }}
              animate={{ opacity: 1, y: 0 }}
              key={i}
              style={{ display: 'flex', justifyContent: m.role === "user" ? "flex-end" : "flex-start" }}
            >
              {m.role === "bot" && (
                <div style={{ background: 'linear-gradient(135deg, var(--primary), var(--accent))', width: '32px', height: '32px', borderRadius: '50%', display: 'flex', alignItems: 'center', justifyContent: 'center', flexShrink: 0, marginRight: '1rem' }}>
                  <span style={{ color: 'white', fontWeight: 'bold', fontSize: '14px' }}>AI</span>
                </div>
              )}
              <div style={{
                maxWidth: '80%',
                padding: '1rem 1.25rem',
                borderRadius: '1rem',
                backgroundColor: m.role === "user" ? 'var(--primary)' : 'var(--card)',
                color: m.role === "user" ? 'var(--primary-foreground)' : 'var(--foreground)',
                border: m.role === "bot" ? '1px solid var(--border)' : 'none',
                boxShadow: 'var(--shadow)',
                fontSize: '0.95rem',
                lineHeight: 1.6
              }}>
                {m.role === "bot" ? (
                  <>
                    <Markdown>{m.text}</Markdown>
                    {m.sources && m.sources.length > 0 && (() => {

                      // ── Deduplicate by file + page keeping highest hnsw_score ──
                      const uniqueSourcesMap = new Map();

                      m.sources.forEach((src) => {
                        const key = `${src.file_name}_${src.page_number}`;

                        const existing = uniqueSourcesMap.get(key);

                        const currentScore = src.hnsw_score || 0;
                        const existingScore = existing?.hnsw_score || 0;

                        if (!existing || currentScore > existingScore) {
                          uniqueSourcesMap.set(key, src);
                        }
                      });

                      // ── Sort by highest semantic relevance ──
                      const uniqueSources = Array.from(uniqueSourcesMap.values())
                        .sort((a, b) => (b.hnsw_score || 0) - (a.hnsw_score || 0))
                        .slice(0, 5); // optional top limit

                      return (
                        <div
                          style={{
                            marginTop: '0.75rem',
                            paddingTop: '0.75rem',
                            borderTop: '1px solid var(--border)'
                          }}
                        >
                          <p
                            style={{
                              fontSize: '0.75rem',
                              color: 'var(--muted-foreground)',
                              marginBottom: '0.4rem',
                              fontWeight: 600
                            }}
                          >
                            Sources
                          </p>

                          <div
                            style={{
                              display: 'flex',
                              flexWrap: 'wrap',
                              gap: '0.4rem'
                            }}
                          >
                            {uniqueSources.map((src, si) => (
                              <button
                                key={si}
                                onClick={() => {
                                  setJumpTarget({
                                    fileName: src.file_name,
                                    page: src.page_number,
                                    ts: Date.now(),
                                  });
                                }}
                                style={{
                                  fontSize: '0.7rem',
                                  padding: '0.2rem 0.5rem',
                                  borderRadius: '999px',
                                  backgroundColor: 'rgba(59,130,246,0.12)',
                                  color: 'var(--primary)',
                                  border: '1px solid rgba(59,130,246,0.25)',
                                  display: 'flex',
                                  alignItems: 'center',
                                  gap: '0.25rem',
                                  cursor: 'pointer',
                                }}
                              >
                                <FileText size={10} />
                                {src.file_name} · p.{src.page_number}
                              </button>
                            ))}
                          </div>
                        </div>
                      );
                    })()}
                  </>
                ) : (
                  m.text
                )}
              </div>
            </motion.div>
          ))}

          {loading && (
            <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} style={{ display: 'flex', justifyContent: 'flex-start' }}>
              <div style={{ background: 'linear-gradient(135deg, var(--primary), var(--accent))', width: '32px', height: '32px', borderRadius: '50%', display: 'flex', alignItems: 'center', justifyContent: 'center', flexShrink: 0, marginRight: '1rem' }}>
                <span style={{ color: 'white', fontWeight: 'bold', fontSize: '14px' }}>AI</span>
              </div>
              <div style={{
                padding: '1rem 1.25rem',
                borderRadius: '1rem',
                backgroundColor: 'var(--card)',
                border: '1px solid var(--border)',
                display: 'flex',
                alignItems: 'center',
                gap: '0.5rem'
              }}>
                <span style={{ width: '6px', height: '6px', background: 'var(--primary)', borderRadius: '50%', animation: 'pulse 1.5s infinite' }} />
                <span style={{ width: '6px', height: '6px', background: 'var(--primary)', borderRadius: '50%', animation: 'pulse 1.5s infinite 0.2s' }} />
                <span style={{ width: '6px', height: '6px', background: 'var(--primary)', borderRadius: '50%', animation: 'pulse 1.5s infinite 0.4s' }} />
              </div>
            </motion.div>
          )}
          <div ref={endRef} />
        </div>

        <div style={{ padding: '1.5rem', borderTop: '1px solid var(--border)', backgroundColor: 'var(--card)' }}>
          <div style={{
            position: 'relative',
            display: 'flex',
            alignItems: 'flex-end',
            backgroundColor: 'var(--background)',
            border: '1px solid var(--border)',
            borderRadius: '1rem',
            padding: '0.5rem'
          }}>
            <textarea
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              onKeyDown={onKeyDown}
              placeholder="Ask a question about the document... (Press Enter to send)"
              rows={1}
              style={{
                flex: 1,
                resize: 'none',
                padding: '0.75rem',
                border: 'none',
                backgroundColor: 'transparent',
                color: 'var(--foreground)',
                outline: 'none',
                fontSize: '0.95rem',
                minHeight: '44px',
                maxHeight: '120px'
              }}
              className="custom-scrollbar"
            />
            <button
              onClick={handleAsk}
              disabled={!query.trim() || loading}
              style={{
                background: query.trim() ? 'var(--primary)' : 'var(--muted)',
                color: query.trim() ? 'var(--primary-foreground)' : 'var(--muted-foreground)',
                border: 'none',
                borderRadius: '50%',
                width: '40px',
                height: '40px',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                cursor: query.trim() ? 'pointer' : 'not-allowed',
                transition: 'all 0.2s ease',
                margin: '4px'
              }}
            >
              <Send size={18} style={{ transform: 'translateX(-1px)' }} />
            </button>
          </div>
          <p style={{ textAlign: 'center', fontSize: '0.75rem', color: 'var(--muted-foreground)', marginTop: '0.75rem' }}>
            Doc_Explorer can make mistakes. Consider verifying important information.
          </p>
        </div>
      </div>
    </div>
  );
}

function PdfViewer({ docParam, jumpTarget, isExpanded }) {
  const [numPages, setNumPages] = useState(null);
  const [pageNumber, setPageNumber] = useState(1);
  const [pageBlobs, setPageBlobs] = useState([]);
  const fileName = docParam.split('/').pop(); // only for display

  useEffect(() => {
    if (!jumpTarget) return;

    // match current viewer file
    if (jumpTarget.fileName !== fileName) return;

    setPageNumber(jumpTarget.page);
  }, [jumpTarget, fileName]);

  console.log("docParam:", docParam, typeof docParam);
  function onDocumentLoadSuccess({ numPages }) {
    setNumPages(numPages);
  }

  useEffect(() => {
    fetch(`/pages/${encodeURIComponent(fileName)}`)
      .then(r => r.ok ? r.json() : null)
      .then(data => {
        if (data && data.pages) setPageBlobs(data.pages);
      })
      .catch(() => { });
  }, [docParam]);

  const handleDownloadFull = () => {
    window.open(`/download-full/${encodeURIComponent(docParam)}`, '_blank');
  };

  const handleDownloadPage = () => {
    window.open(`/download-page/${encodeURIComponent(docParam)}?page_number=${pageNumber}`, '_blank');
  };

  return (
    <div style={{ marginBottom: '3rem', width: '100%', display: 'flex', flexDirection: 'column', alignItems: 'center' }}>
      <div style={{
        width: '100%',
        maxWidth: isExpanded ? '550px' : '750px',
        padding: '0.75rem 1rem',
        backgroundColor: 'var(--card)',
        border: '1px solid var(--border)',
        borderRadius: '8px 8px 0 0',
        display: 'flex',
        justifyContent: 'space-between',
        alignItems: 'center'
      }}>
        <div style={{ fontWeight: 600, fontSize: '0.875rem', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', maxWidth: '40%' }}>
          {docParam.split('/').pop()}
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', background: 'var(--muted)', padding: '0.25rem', borderRadius: 'var(--radius)' }}>
          <button
            className="btn btn-ghost"
            style={{ padding: '0.25rem' }}
            onClick={() => setPageNumber(p => Math.max(1, p - 1))}
            disabled={pageNumber <= 1}
          >
            <ChevronLeft size={16} />
          </button>
          <span style={{ fontSize: '0.75rem', margin: '0 0.25rem', fontFamily: 'monospace' }}>
            {pageNumber} / {numPages || '-'}
          </span>
          <button
            className="btn btn-ghost"
            style={{ padding: '0.25rem' }}
            onClick={() => setPageNumber(p => Math.min(numPages || p, p + 1))}
            disabled={pageNumber >= (numPages || Infinity)}
          >
            <ChevronRight size={16} />
          </button>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: '0.4rem' }}>
          <button
            className="btn btn-ghost"
            style={{ padding: '0.25rem 0.6rem', fontSize: '0.75rem', display: 'flex', alignItems: 'center', gap: '0.3rem' }}
            onClick={handleDownloadPage}
            title={`Download page ${pageNumber}`}
          >
            <Download size={13} /> Page
          </button>
          <button
            className="btn btn-secondary"
            style={{ padding: '0.25rem 0.6rem', fontSize: '0.75rem', display: 'flex', alignItems: 'center', gap: '0.3rem' }}
            onClick={handleDownloadFull}
            title="Download full PDF"
          >
            <Download size={13} /> Full PDF
          </button>
        </div>
      </div>

      <div style={{ boxShadow: 'var(--shadow-lg)', backgroundColor: 'white', minHeight: '800px', minWidth: isExpanded ? '500px' : '700px' }}>
        <Document
          file={`/download/${encodeURIComponent(docParam)}`}
          onLoadSuccess={onDocumentLoadSuccess}
          loading={<div style={{ padding: '4rem', color: 'black', display: 'flex', justifyContent: 'center' }}><Loader2 className="animate-spin" /></div>}
          error={
            <div style={{ height: '800px', display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', color: '#666', background: 'white' }}>
              <FileText size={48} style={{ opacity: 0.2, marginBottom: '1rem' }} />
              <p>Failed to load document preview.</p>
            </div>
          }
        >
          <Page pageNumber={pageNumber} width={isExpanded ? 500 : 700} />
        </Document>
      </div>
    </div>
  );
}
