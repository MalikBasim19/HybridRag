import logging
from concurrent.futures import ThreadPoolExecutor
from .repository import Repository
from .rag.extraction import extract
from .rag.chunking import chunk
from .rag.bm25 import build_bm25
from .rag.retriever import hybrid
from .rag.generation import generate, stream_generate

log = logging.getLogger(__name__)
class Service:
    def __init__(self, settings, vectors, extractor=extract, generator=generate, stream_generator=stream_generate):
        self.settings, self.vectors = settings, vectors
        self.extractor, self.generator = extractor, generator
        self.stream_generator = stream_generator
        self.lock = vectors.lock
        self.repo = Repository(settings.storage / 'corpus.sqlite3')
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix='ingestion')
        # Recover pending deletion or partial ingestion before permitting queries.
        for d in self.repo.list():
            if d['status'] == 'deleting':
                self.vectors.delete(d['id'])
                self.repo.delete(d['id'])
                (settings.storage / 'uploads' / (d['id']+'.pdf')).unlink(missing_ok=True)
            elif d['status'] != 'indexed':
                self.vectors.delete(d['id'])
                self.repo.status(d['id'], 'failed', 'Interrupted or failed ingestion; delete and re-upload to retry')
        self.bm25 = build_bm25(self.repo.corpus())
    def submit(self, id):
        self.pool.submit(self.ingest, id)
    def ingest(self, id):
        path = self.settings.storage / 'uploads' / (id+'.pdf')
        try:
            with self.lock:
                self.repo.status(id, 'extracting')
                filename = self.repo.get(id)['filename']
            chunks = chunk(self.extractor(path, {'document_id': id, 'filename': filename, 'source': str(path)}))
            with self.lock:
                self.repo.status(id, 'indexing')
            self.vectors.add(chunks)
            with self.lock:
                self.repo.save_chunks(id, chunks)
                self.bm25 = build_bm25(self.repo.corpus())
        except Exception:
            log.exception('Ingestion failed for %s', id)
            with self.lock:
                try:
                    self.vectors.delete(id)
                except Exception:
                    log.exception('Cleanup deferred until restart for %s', id)
                self.repo.status(id, 'failed', 'PDF processing failed; see backend logs. Delete and upload again to retry.')
    def delete(self, id):
        with self.lock:
            d = self.repo.get(id)
            if not d:
                raise KeyError(id)
            if d['status'] in ('queued', 'extracting', 'indexing'):
                raise ValueError('Wait for processing to finish before deleting')
            self.repo.status(id, 'deleting')
            self.bm25 = build_bm25(self.repo.corpus())
            self.vectors.delete(id)
            self.repo.delete(id)
            self.bm25 = build_bm25(self.repo.corpus())
            (self.settings.storage / 'uploads' / (id+'.pdf')).unlink(missing_ok=True)
    def _retrieve(self, question):
        with self.lock:
            if self.bm25 is None:
                raise ValueError('Upload and index a PDF before asking a question')
            ids = [d['id'] for d in self.repo.list() if d['status'] == 'indexed']
            docs = hybrid(self.vectors, self.bm25, ids).invoke(question)
            selected, used = [], 0
            for doc in docs:
                if used + len(doc.page_content) <= self.settings.max_context_chars:
                    selected.append(doc)
                    used += len(doc.page_content)
            if not selected:
                raise ValueError('Retrieved tables exceed the context budget; increase MAX_CONTEXT_CHARS')
        return selected, {'sources': [dict(document_id=d.metadata['document_id'], filename=d.metadata['filename'],
            chunk_id=d.metadata['chunk_id'], type=d.metadata['type'], pages=d.metadata.get('pages'), snippet=d.page_content) for d in selected],
            'context_limited': len(selected) != len(docs)}
    def chat(self, question):
        selected, metadata = self._retrieve(question)
        return dict(metadata, answer=self.generator(self.settings, question, selected))
    def stream_chat(self, question):
        # Retrieve before sending headers so validation errors retain HTTP status codes.
        selected, metadata = self._retrieve(question)
        def events():
            yield dict(metadata, type='sources')
            stream = self.stream_generator(self.settings, question, selected)
            try:
                for text in stream:
                    yield {'type': 'token', 'text': text}
            finally:
                stream.close()
            yield {'type': 'done'}
        return events()
    def close(self):
        self.pool.shutdown(wait=True)
        self.vectors.close()
        self.repo.db.close()
