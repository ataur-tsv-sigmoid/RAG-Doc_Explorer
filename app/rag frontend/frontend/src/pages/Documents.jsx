import { useState, useEffect, useRef } from "react";
import { useNavigate } from "react-router-dom";
import { UploadCloud, FileText, CheckCircle, Play, Download, Loader2, MessageSquare } from "lucide-react";
import { motion, AnimatePresence } from "framer-motion";

export default function Documents() {
  const [file, setFile] = useState(null);
  const [uploading, setUploading] = useState(false);
  const [documents, setDocuments] = useState([]);
  const [loadingDocs, setLoadingDocs] = useState(true);
  const [dragActive, setDragActive] = useState(false);
  const [selectedDocs, setSelectedDocs] = useState(new Set());
  const inputRef = useRef(null);
  const navigate = useNavigate();
  
//   const formatFileName = (path) => {
//   const base = path.split('/').pop(); // remove pdfs/raw/
//   return base.replace(/_/g, ' ');
// };

  const fetchDocuments = async () => {
    try {
      setLoadingDocs(true);
      const res = await fetch("/pdfs");
      if (res.ok) {
        const data = await res.json();
        setDocuments(data.results || []);
      }
    } catch (e) {
      console.error("Failed to fetch documents", e);
      // For demonstration if backend is down:
      setDocuments(["example_report_2023.pdf", "q3_financials.pdf", "product_roadmap.pdf"]);
    } finally {
      setLoadingDocs(false);
    }
  };

  useEffect(() => {
    fetchDocuments();
  }, []);

  const handleDrag = (e) => {
    e.preventDefault();
    e.stopPropagation();
    if (e.type === "dragenter" || e.type === "dragover") {
      setDragActive(true);
    } else if (e.type === "dragleave") {
      setDragActive(false);
    }
  };

  const handleDrop = (e) => {
    e.preventDefault();
    e.stopPropagation();
    setDragActive(false);
    if (e.dataTransfer.files && e.dataTransfer.files[0]) {
      setFile(e.dataTransfer.files[0]);
    }
  };

  const handleUpload = async () => {
    if (!file) return;
    setUploading(true);

    const formData = new FormData();
    formData.append("file", file);

    try {
      const res = await fetch("/upload", {
        method: "POST",
        body: formData,
      });
      
      if (!res.ok) {
        const errorText = await res.text();
        alert(`Upload failed: ${res.status} - ${errorText}`);
      } else {
        fetchDocuments(); // Refresh list after upload
      }
    } catch (e) {
      console.error("Upload failed", e);
    }

    setUploading(false);
    setFile(null);
  };

  /*const handleProcess = async (fileName) => {
    try {
      await fetch(`/process/${fileName}`, { method: "POST" });
      alert(`Processing started for ${fileName}`);
    } catch (e) {
      console.error("Process failed", e);
    }
  };*/

  const handleDownload = (fileName) => {
    // const cleanName = fileName.replace(/'/g, '');
    window.open(`/download-full/${encodeURIComponent(fileName)}`, '_blank');
  };

  return (
    <div className="page-content" style={{ maxWidth: '1000px', margin: '0 auto', display: 'flex', flexDirection: 'column', gap: '2rem' }}>
      
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <div>
          <h1 style={{ fontSize: '1.75rem', marginBottom: '0.25rem' }}>Document Library</h1>
          <p style={{ color: 'var(--muted-foreground)' }}>Upload and manage your PDFs for analysis</p>
        </div>
        {selectedDocs.size > 0 && (
          <button 
            className="btn btn-primary" 
            style={{ padding: '0.75rem 1.5rem', boxShadow: 'var(--shadow-lg)', animation: 'pulse-ring 2s infinite' }}
            onClick={() => {
              const encodedDocs = Array.from(selectedDocs)
                .map(d => encodeURIComponent(d))
                .join(',');
              navigate(`/workspace?docs=${encodedDocs}`);
            }}
          >
            <MessageSquare size={20} />
            Chat with {selectedDocs.size} Selected {selectedDocs.size === 1 ? 'Document' : 'Documents'}
          </button>
        )}
      </div>

      <div className="card" style={{ flexShrink: 0 }}>
        <div className="card-header">
          <h2 className="card-title" style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
            <UploadCloud size={20} className="text-primary" />
            Upload Document
          </h2>
        </div>
        <div className="card-content">
          <div 
            onDragEnter={handleDrag}
            onDragLeave={handleDrag}
            onDragOver={handleDrag}
            onDrop={handleDrop}
            style={{
              border: `2px dashed ${dragActive ? 'var(--primary)' : 'var(--border)'}`,
              borderRadius: 'var(--radius)',
              padding: '3rem 2rem',
              textAlign: 'center',
              backgroundColor: dragActive ? 'rgba(59, 130, 246, 0.05)' : 'var(--background)',
              transition: 'all 0.2s ease',
              cursor: 'pointer'
            }}
            onClick={() => inputRef.current?.click()}
          >
            <input 
              ref={inputRef}
              type="file" 
              accept=".pdf" 
              style={{ display: 'none' }} 
              onChange={(e) => setFile(e.target.files[0])}
            />
            
            <>
              {file ? (
                <div 
                  style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: '1rem' }}
                >
                  <div style={{ background: 'var(--muted)', padding: '1rem', borderRadius: '50%' }}>
                    <FileText size={32} color="var(--primary)" />
                  </div>
                  <div>
                    <p style={{ fontWeight: 600, color: 'var(--foreground)' }}>{file.name}</p>
                    <p style={{ fontSize: '0.875rem', color: 'var(--muted-foreground)' }}>
                      {(file.size / 1024 / 1024).toFixed(2)} MB
                    </p>
                  </div>
                  <div style={{ display: 'flex', gap: '1rem', marginTop: '1rem' }}>
                    <button 
                      className="btn btn-secondary" 
                      onClick={(e) => { e.stopPropagation(); setFile(null); }}
                      disabled={uploading}
                    >
                      Cancel
                    </button>
                    <button 
                      className="btn btn-primary" 
                      onClick={(e) => { e.stopPropagation(); handleUpload(); }}
                      disabled={uploading}
                    >
                      {uploading ? (
                        <><Loader2 size={16} className="animate-spin" /> Uploading...</>
                      ) : (
                        <><UploadCloud size={16} /> Upload to Library</>
                      )}
                    </button>
                  </div>
                </div>
              ) : (
                <div
                  style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: '1rem' }}
                >
                  <div style={{ background: 'var(--muted)', padding: '1rem', borderRadius: '50%' }}>
                    <UploadCloud size={32} color="var(--muted-foreground)" />
                  </div>
                  <div>
                    <p style={{ fontWeight: 500, fontSize: '1.1rem', marginBottom: '0.25rem' }}>
                      Click or drag a file to this area to upload
                    </p>
                    <p style={{ color: 'var(--muted-foreground)', fontSize: '0.875rem' }}>
                      Supports single PDF upload
                    </p>
                  </div>
                </div>
              )}
            </>
          </div>
        </div>
      </div>

      <div className="card" style={{ flexShrink: 0, display: 'flex', flexDirection: 'column', maxHeight: '500px' }}>
        <div className="card-header">
          <h2 className="card-title">Available Documents</h2>
          <p className="card-description">View, process, and manage your uploaded PDFs</p>
        </div>
        
        <div className="custom-scrollbar" style={{ padding: '0', overflowY: 'auto', flex: 1 }}>
          {loadingDocs ? (
            <div style={{ padding: '3rem', display: 'flex', justifyContent: 'center' }}>
              <Loader2 size={24} className="animate-spin" color="var(--primary)" />
            </div>
          ) : documents.length === 0 ? (
            <div style={{ padding: '3rem', textAlign: 'center', color: 'var(--muted-foreground)' }}>
              <FileText size={32} style={{ margin: '0 auto 1rem auto', opacity: 0.5 }} />
              <p>No documents found in the library.</p>
            </div>
          ) : (
            <div style={{ display: 'flex', flexDirection: 'column' }}>
              {documents.map((doc, i) => {
                const fileName = typeof doc === "string" ? doc : doc.file_name;
                const blobPath = typeof doc === "string" ? doc : doc.blob_path;
                return (
                  <div 
                    key={i} 
                    style={{ 
                      display: 'flex', 
                      alignItems: 'center', 
                      justifyContent: 'space-between',
                      padding: '1rem 1.5rem',
                      borderBottom: i < documents.length - 1 ? '1px solid var(--border)' : 'none',
                      transition: 'background-color 0.2s',
                    }}
                    onMouseOver={(e) => e.currentTarget.style.backgroundColor = 'var(--muted)'}
                    onMouseOut={(e) => e.currentTarget.style.backgroundColor = 'transparent'}
                  >
                    <div style={{ display: 'flex', alignItems: 'center', gap: '1rem' }}>
                      <input 
                        type="checkbox" 
                        checked={selectedDocs.has(blobPath)}

                        onChange={(e) => {
                          const newSet = new Set(selectedDocs);
                          if (e.target.checked) newSet.add(blobPath);   // ✅ FIX
                          else newSet.delete(blobPath);
                          setSelectedDocs(newSet);
                        }}
                        style={{ width: '1.25rem', height: '1.25rem', cursor: 'pointer', accentColor: 'var(--primary)' }}
                      />
                      <div style={{ background: 'rgba(59, 130, 246, 0.1)', padding: '0.5rem', borderRadius: '8px' }}>
                        <FileText size={20} color="var(--primary)" />
                      </div>
                      <div>
                        <p style={{ fontWeight: 500 }}>{fileName}</p>
                        <p style={{ fontSize: '0.75rem', color: 'var(--muted-foreground)', display: 'flex', alignItems: 'center', gap: '4px', marginTop: '4px' }}>
                          <CheckCircle size={12} color="var(--accent)" /> Ready for Processing
                        </p>
                      </div>
                    </div>
                    
                    <div style={{ display: 'flex', gap: '0.5rem' }}>
                      <button 
                        className="btn btn-primary" 
                        onClick={() => {
                          const cleanName = fileName.replace(/'/g, '');
                          navigate(`/workspace?doc=${encodeURIComponent(blobPath)}`);
                        }}
                        title="Chat with Document in Workspace"
                        style={{ padding: '0.5rem 1rem' }}
                      >
                        <MessageSquare size={16} /> Chat
                      </button>
                      {/*<button className="btn btn-secondary" onClick={() => handleProcess(fileName)} title="Process Document">
                        <Play size={16} />
                      </button>*/}
                      <button className="btn btn-ghost" onClick={() => handleDownload(fileName)} title="Download Document">
                        <Download size={16} />
                      </button>
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
