import json
import sqlite3
from langchain_core.documents import Document

class Repository:
    def __init__(self, path):
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
        PRAGMA journal_mode=WAL;
        CREATE TABLE IF NOT EXISTS documents(id TEXT PRIMARY KEY, filename TEXT, status TEXT, error TEXT, chunk_count INTEGER DEFAULT 0);
        CREATE TABLE IF NOT EXISTS chunks(id TEXT PRIMARY KEY, document_id TEXT, content TEXT, metadata TEXT);
        """)
    def list(self):
        return [dict(r) for r in self.db.execute('SELECT * FROM documents ORDER BY rowid')]
    def get(self, id):
        r = self.db.execute('SELECT * FROM documents WHERE id=?', (id,)).fetchone()
        return dict(r) if r else None
    def reserve(self, id, filename):
        with self.db:
            self.db.execute("INSERT INTO documents(id,filename,status) VALUES(?,?,'queued')", (id,filename))
    def status(self, id, status, error=None, count=0):
        with self.db:
            self.db.execute('UPDATE documents SET status=?,error=?,chunk_count=? WHERE id=?', (status,error,count,id))
    def save_chunks(self, id, chunks):
        with self.db:
            self.db.execute('DELETE FROM chunks WHERE document_id=?', (id,))
            self.db.executemany('INSERT INTO chunks VALUES(?,?,?,?)', [(d.metadata['chunk_id'],id,d.page_content,json.dumps(d.metadata)) for d in chunks])
            self.db.execute("UPDATE documents SET status='indexed',error=NULL,chunk_count=? WHERE id=?", (len(chunks),id))
    def corpus(self):
        return [Document(page_content=r['content'], metadata=json.loads(r['metadata'])) for r in self.db.execute("SELECT c.* FROM chunks c JOIN documents d ON d.id=c.document_id WHERE d.status='indexed' ORDER BY c.rowid")]
    def delete(self, id):
        with self.db:
            self.db.execute('DELETE FROM chunks WHERE document_id=?', (id,))
            self.db.execute('DELETE FROM documents WHERE id=?', (id,))
