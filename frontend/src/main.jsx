import React, {useState, useEffect, useRef} from 'react';
import {createRoot} from 'react-dom/client';
import {api} from './api';
import './style.css';

const preparationStages = ['queued', 'extracting', 'indexing'];
const statusLabels = {queued:'Waiting to process', extracting:'Reading PDF with Docling', indexing:'Building searchable index', indexed:'Ready to ask', failed:'Processing failed'};
function DocumentProgress({status}) {
  const current = preparationStages.indexOf(status);
  return <ol className="preparation-steps" aria-label="PDF preparation progress">
    {['Queued', 'Read PDF', 'Build index', 'Ready'].map((label, i) => <li key={label} className={i < current ? 'complete' : i === current ? 'current' : ''} aria-current={i === current ? 'step' : undefined}><span aria-hidden="true">{i < current ? '✓' : i + 1}</span>{label}</li>)}
  </ol>;
}

function App() {
  const [docs, setDocs] = useState([]), [messages, setMessages] = useState([]);
  const [question, setQuestion] = useState(''), [busy, setBusy] = useState(false);
  const [uploading, setUploading] = useState(false), [error, setError] = useState('');
  const [notice, setNotice] = useState(''), [configured, setConfigured] = useState(true);
  const fileInput = useRef(null), bottom = useRef(null);
  const indexed = docs.filter(d => d.status === 'indexed').length;
  const processing = docs.filter(d => preparationStages.includes(d.status));
  async function refresh() {
    const [items, health] = await Promise.all([api('/api/documents'), api('/health')]);
    setDocs(items); setConfigured(health.openai_configured);
  }
  useEffect(() => {
    let active = true;
    const poll = () => { if (active) refresh().catch(e => {if(active) setError(e.message)}); };
    poll(); const timer = setInterval(poll, 2500);
    return () => {active = false; clearInterval(timer)};
  }, []);
  useEffect(() => bottom.current?.scrollIntoView({behavior:'smooth'}), [messages, busy]);
  async function upload(files) {
    if (!files.length) return;
    setUploading(true); setError(''); setNotice('');
    const data = new FormData(); Array.from(files).forEach(f => data.append('files', f));
    try {
      const result = await api('/api/documents/upload', {method:'POST', body:data});
      setNotice(result.documents.map(d => `${d.uploaded_filename || d.filename}: ${d.duplicate ? 'already uploaded' : d.status}${d.error ? ` — ${d.error}` : ''}`).join('\n'));
      await refresh();
    } catch(e) {setError(e.message)} finally {setUploading(false); if(fileInput.current) fileInput.current.value = ''}
  }
  async function remove(id) {
    setError('');
    try {await api(`/api/documents/${id}`, {method:'DELETE'}); await refresh()} catch(e) {setError(e.message)}
  }
  async function ask(e) {
    e.preventDefault(); const text = question.trim(); if(!text || busy) return;
    setQuestion(''); setError(''); setMessages(m => [...m, {role:'user', text}]); setBusy(true);
    try {
      const result = await api('/api/chat', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({question:text})});
      setMessages(m => [...m, {role:'assistant', text:result.answer, sources:result.sources, limited:result.context_limited}]);
    } catch(e) {setError(e.message); setQuestion(text)} finally {setBusy(false)}
  }
  return <div className="app">
    <aside><a className="brand" href="/">◈ <span>DocLens<span className="brand-dot">.</span></span></a>
      <div className="section-label">YOUR WORKSPACE</div><h2>Document library <span>{docs.length}</span></h2>
      <p className="muted">One collection. All your knowledge.</p>
      <label className={`upload ${uploading ? 'disabled' : ''}`} onDragOver={e => e.preventDefault()} onDrop={e => {e.preventDefault(); if(!uploading) upload(e.dataTransfer.files)}}>
        <span className="upload-icon">{uploading ? <span className="spinner"/> : '↑'}</span><strong>{uploading ? 'Uploading PDFs…' : 'Add PDF documents'}</strong><span>{uploading ? 'Sending your files. Keep this page open.' : 'Click to browse or drop files here'}</span><small>Up to 80 PDFs · 50 MB each by default</small>
        <input ref={fileInput} type="file" accept="application/pdf,.pdf" multiple disabled={uploading} onChange={e => upload(e.target.files)}/>
      </label>
      <div className="documents">{docs.length === 0 && <p className="empty-library">Your library is ready for its first PDF.</p>}
        {docs.map(d => <article className="document" key={d.id}><div className="pdf-icon">PDF</div><div className="document-info"><strong title={`${d.filename} · ${d.id}`}>{d.filename}</strong><span className={`status ${d.status}`}>{preparationStages.includes(d.status) && <span className="spinner" aria-hidden="true"/>}{statusLabels[d.status] || d.status} {d.status==='indexed' && `· ${d.chunk_count} chunks`}</span>{preparationStages.includes(d.status) && <DocumentProgress status={d.status}/ >}{d.error && <p className="file-error">{d.error}</p>}</div><button className="delete" aria-label={`Delete ${d.filename}`} disabled={preparationStages.includes(d.status)} onClick={() => remove(d.id)}>×</button></article>)}
      </div><div className="sidebar-footer"><span className="dot"/> Local workspace <small>Semantic + keyword retrieval</small></div>
    </aside>
    <main><header><div><span className="section-label">DOCUMENT INTELLIGENCE</span><h1>Ask your documents</h1></div><span className="collection-tag">{indexed} ready{processing.length > 0 && ` · ${processing.length} preparing`} · Hybrid 50 / 50</span></header>
      <div role="status" aria-live="polite" aria-atomic="true">{(uploading || processing.length > 0) && <div className="preparation-banner"><span className="spinner" aria-hidden="true"/><div><strong>{uploading ? 'Uploading your PDFs…' : `Preparing ${processing.length === 1 ? 'your PDF' : `${processing.length} PDFs`} for questions…`}</strong><small>{indexed ? 'You can keep asking questions about ready documents. New PDFs join automatically when ready.' : 'Questions will unlock automatically when your first PDF is ready.'}</small></div></div>}{!uploading && !processing.length && indexed > 0 && <p className="ready-notice">✓ {indexed === 1 ? 'Your PDF is' : `${indexed} PDFs are`} ready for questions.</p>}</div>
      {!configured && <div className="alert">Set OPENAI_API_KEY in backend/.env and restart the backend to begin.</div>}
      {error && <div className="alert" role="alert">{error}<button onClick={() => setError('')} aria-label="Dismiss error">×</button></div>}
      {notice && <details className="notice"><summary>Upload results</summary><pre>{notice}</pre></details>}
      <div className="conversation" aria-live="polite">{!messages.length && <div className="welcome"><div className="spark">✧</div><span className="section-label">ANSWERS, GROUNDED IN YOUR FILES</span><h2>From pages to perspective.</h2><p>Explore your PDFs with a question.<br/>Get clear answers with the source text close at hand.</p><div className="features"><span>↗ Semantic understanding</span><span>⌕ Exact keyword matching</span><span>▤ Tables preserved</span></div></div>}
        {messages.map((m,i) => <article key={i} className={`message ${m.role}`}><div className="message-label">{m.role === 'user' ? 'YOU' : '◈ DOCLENS'}</div><div className="answer">{m.text}</div>{m.limited && <p className="muted">Some retrieved chunks exceeded the context budget.</p>}{m.sources && <div className="sources"><span className="section-label">RETRIEVED REFERENCES · {m.sources.length}</span>{m.sources.map((s,j) => <details key={s.chunk_id}><summary>{j+1}. {s.filename} <small>{s.type}{s.pages?.length ? ` · pp. ${s.pages.join(', ')}` : ''}</small></summary><small>Document {s.document_id.slice(0,12)}</small><pre>{s.snippet}</pre></details>)}</div>}</article>)}
        {busy && <div className="thinking">Searching your collection and composing an answer…</div>}<div ref={bottom}/>
      </div><div className="composer-wrap"><form className="composer" onSubmit={ask}><label className="sr-only" htmlFor="question">Your question</label><textarea id="question" rows="2" maxLength="8000" placeholder={indexed ? 'Ask a question about your documents…' : uploading || processing.length ? 'Preparing your PDF — questions will unlock when ready…' : 'Upload a PDF to start exploring…'} value={question} disabled={!indexed || !configured} onChange={e => setQuestion(e.target.value)} onKeyDown={e => {if(e.key==='Enter' && !e.shiftKey){e.preventDefault(); ask(e)}}}/><button disabled={!indexed || !configured || busy || !question.trim()} aria-label="Send question">↑</button></form><p>GPT-4.1-mini · Answers use retrieved context. Review references for accuracy.</p></div>
    </main></div>;
}
createRoot(document.getElementById('root')).render(<App/>);
