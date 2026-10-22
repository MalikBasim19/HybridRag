"""Offline integration: real persistent Qdrant/BM25, deterministic embeddings and PDF extractor."""
import hashlib
import json
import time
from pathlib import Path
import pytest
from fastapi.testclient import TestClient
from langchain_core.documents import Document
from langchain_core.embeddings import Embeddings
from langchain_qdrant import QdrantVectorStore
from app.config import Settings
from app.main import create_app
from app.service import Service
from app.rag.vector_store import Vectors
from app.rag.chunking import chunk

class OfflineEmbeddings(Embeddings):
    def embed_documents(self, texts):
        return [self.embed_query(t) for t in texts]
    def embed_query(self, text):
        vector = [0.0] * 1536
        for token in text.lower().split():
            vector[int(hashlib.sha256(token.encode()).hexdigest()[:8],16) % 1536] += 1
        return vector

def extractor(path, metadata):
    text = path.read_bytes().decode()
    if 'BROKEN' in text:
        raise ValueError('Deliberate extraction failure')
    return [Document(page_content=text, metadata=dict(metadata,type='text',pages=[1]))]

def generator(settings, question, docs):
    return docs[0].page_content

def stream_generator(settings, question, docs):
    yield 'First chunk '
    yield 'with Unicode: café\nsecond line'

@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setattr('app.rag.vector_store.create_embeddings', lambda _: OfflineEmbeddings())
    settings = Settings(data_dir=tmp_path, openai_api_key='offline-test', _env_file=None)
    def factory(s):
        return Service(s, Vectors(s), extractor=extractor, generator=generator, stream_generator=stream_generator)
    return settings, factory

def wait(client, count):
    deadline = time.monotonic()+60
    while time.monotonic() < deadline:
        docs = client.get('/api/documents').json()
        if len(docs)==count and all(d['status'] in ('indexed','failed') for d in docs):
            return docs
        time.sleep(.02)
    pytest.fail('Ingestion did not complete')

@pytest.mark.parametrize('count', [1, 3, 80])
def test_staged_ingestion_chat_restart_delete(setup, count):
    settings, factory = setup
    with TestClient(create_app(settings, factory)) as client:
        files = [('files', ('same-name.pdf',f'%PDF-1.4 vaccine protocol item{i} dose {i+10} mg'.encode(),'application/pdf')) for i in range(count)]
        response = client.post('/api/documents/upload', files=files)
        assert response.status_code == 202
        docs = wait(client, count)
        assert all(d['status']=='indexed' for d in docs)
        assert len({d['id'] for d in docs}) == count
        duplicate = client.post('/api/documents/upload',files=[files[0]]).json()['documents'][0]
        assert duplicate['duplicate'] is True
        answer = client.post('/api/chat',json={'question':'vaccine protocol item0 dose'})
        assert answer.status_code == 200
        sources = answer.json()['sources']
        assert sources and all(s['filename']=='same-name.pdf' and s['pages']==[1] for s in sources)
        assert len({s['chunk_id'] for s in sources}) == len(sources)
    with TestClient(create_app(settings, factory)) as client:
        assert client.get('/health').json()['indexed_documents'] == count
        assert client.post('/api/chat',json={'question':'vaccine dose'}).status_code == 200
        for doc in docs:
            assert client.delete('/api/documents/'+doc['id']).status_code == 204
        assert client.post('/api/chat',json={'question':'vaccine'}).status_code == 409
        assert client.get('/api/documents').json() == []
        assert client.app.state.service.vectors.client.count(settings.collection).count == 0

def test_errors_and_recovery(setup):
    settings, factory = setup
    with TestClient(create_app(settings, factory)) as client:
        response = client.post('/api/documents/upload',files=[
            ('files',('../good.pdf',b'%PDF-1.4 valid vaccine study','application/pdf')),
            ('files',('bad.pdf',b'%PDF-1.4 BROKEN','application/pdf')),
            ('files',('fake.pdf',b'not pdf','application/pdf')),
            ('files',('bad.txt',b'%PDF-1.4','text/plain'))])
        assert [d['status'] for d in response.json()['documents']][2:] == ['rejected','rejected']
        docs = wait(client,2)
        assert sorted(d['status'] for d in docs) == ['failed','indexed']
        assert client.post('/api/chat',json={'question':'vaccine'}).status_code == 200
        s = client.app.state.service
        # Simulate a process death after partial Qdrant publication, before SQLite commit.
        id = 'f'*64
        with s.lock:
            s.repo.reserve(id,'interrupted.pdf')
            s.repo.status(id,'indexing')
            s.vectors.add(chunk([Document(page_content='orphaned data',metadata={'document_id':id,'filename':'interrupted.pdf','type':'text'})]))
    with TestClient(create_app(settings,factory)) as client:
        docs = client.get('/api/documents').json()
        assert next(d for d in docs if d['id']==id)['status']=='failed'
        assert client.app.state.service.vectors.client.count(settings.collection).count == 1
        assert client.post('/api/chat',json={'question':' '}).status_code == 422
        assert client.delete('/api/documents/missing').status_code == 404

def test_whole_tables_and_identical_text_attribution():
    table = Document(page_content='| table | value |\n' * 1000, metadata={'document_id':'a','filename':'a.pdf','type':'table'})
    result = chunk([table])
    assert len(result)==1 and result[0].page_content==table.page_content
    assert 'warning' in result[0].metadata
    a = chunk([Document(page_content='identical',metadata={'document_id':'a','type':'text'})])[0]
    b = chunk([Document(page_content='identical',metadata={'document_id':'b','type':'text'})])[0]
    assert a.metadata['chunk_id'] != b.metadata['chunk_id']

def test_streaming_chat_and_failures(setup):
    settings, factory = setup
    with TestClient(create_app(settings, factory)) as client:
        assert client.post('/api/chat/stream', json={'question':'study'}).status_code == 409
        assert client.post('/api/chat/stream', json={'question':' '}).status_code == 422
        settings.openai_api_key = ''
        assert client.post('/api/chat/stream', json={'question':'study'}).status_code == 503
        settings.openai_api_key = 'offline-test'
        client.post('/api/documents/upload', files={'files':('study.pdf', b'%PDF-1.4 study', 'application/pdf')})
        wait(client, 1)
        response = client.post('/api/chat/stream', json={'question':'study'})
        assert response.status_code == 200
        assert response.headers['content-type'].startswith('text/event-stream')
        events = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith('data: ')]
        assert [e['type'] for e in events] == ['sources', 'token', 'token', 'done']
        assert events[0]['sources'][0]['filename'] == 'study.pdf'
        assert events[0]['context_limited'] is False
        assert ''.join(e['text'] for e in events if e['type'] == 'token') == 'First chunk with Unicode: café\nsecond line'
        def failing_stream(*args):
            yield 'Partial answer'
            raise RuntimeError('Private provider error')
        client.app.state.service.stream_generator = failing_stream
        response = client.post('/api/chat/stream', json={'question':'study'})
        events = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith('data: ')]
        assert [e['type'] for e in events] == ['sources', 'token', 'error']
        assert 'Private provider error' not in response.text

def test_model_stream_yields_before_completion(monkeypatch):
    from types import SimpleNamespace
    from app.rag.generation import stream_generate
    closed = []
    def provider(messages):
        assert 'study context' in messages[1].content
        try:
            yield SimpleNamespace(content='First')
            yield SimpleNamespace(content=' second')
            pytest.fail('Provider should be closed before reaching the end')
        finally:
            closed.append(True)
    monkeypatch.setattr('app.rag.generation.ChatOpenAI', lambda **kwargs: SimpleNamespace(stream=provider))
    docs = [Document(page_content='study context', metadata={'filename':'study.pdf', 'document_id':'a'})]
    stream = stream_generate(SimpleNamespace(openai_api_key='offline'), 'question', docs)
    assert next(stream) == 'First'
    assert next(stream) == ' second'
    stream.close()
    assert closed == [True]

def test_partial_ingestion_stays_invisible_and_health_responds(setup):
    from threading import Event
    settings, factory = setup
    with TestClient(create_app(settings, factory)) as client:
        client.post('/api/documents/upload', files={'files':('good.pdf',b'%PDF-1.4 searchable valid study','application/pdf')})
        wait(client,1)
        s = client.app.state.service
        began, proceed = Event(), Event()
        original = s.vectors.add
        def blocked_add(chunks):
            original(chunks)  # Simulate partial vector writes before publication.
            began.set()
            assert proceed.wait(10)
        s.vectors.add = blocked_add
        try:
            client.post('/api/documents/upload',files={'files':('pending.pdf',b'%PDF-1.4 secret pending study','application/pdf')})
            assert began.wait(5)
            assert client.get('/health').json()['indexed_documents']==1
            reply = client.post('/api/chat',json={'question':'secret pending study'})
            assert reply.status_code==200
            assert all(d['filename']=='good.pdf' for d in reply.json()['sources'])
        finally:
            proceed.set()
        assert all(d['status']=='indexed' for d in wait(client,2))

def test_deletion_recovery(setup):
    settings, factory = setup
    with TestClient(create_app(settings,factory)) as client:
        client.post('/api/documents/upload',files={'files':('good.pdf',b'%PDF-1.4 sample study','application/pdf')})
        doc = wait(client,1)[0]
        s = client.app.state.service
        def fail_delete(id):
            raise RuntimeError('Qdrant temporarily unavailable')
        s.vectors.delete = fail_delete
        assert client.delete('/api/documents/'+doc['id']).status_code==502
        assert client.post('/api/chat',json={'question':'study'}).status_code==409
    with TestClient(create_app(settings,factory)) as client:
        assert client.get('/api/documents').json()==[]
        assert client.app.state.service.vectors.client.count(settings.collection).count==0

def test_missing_credentials_and_limits(setup):
    settings, factory = setup
    settings.openai_api_key = ''
    with TestClient(create_app(settings,factory)) as client:
        assert client.get('/health').json()['openai_configured'] is False
        assert client.post('/api/chat',json={'question':'test'}).status_code==503
        assert client.post('/api/documents/upload', files={'files':('a.pdf',b'%PDF-1.4','application/pdf')}).status_code==503
    settings.openai_api_key = 'offline-test'
    settings.max_batch_files = 1
    settings.max_file_mb = 1
    with TestClient(create_app(settings,factory)) as client:
        f = ('files',('a.pdf',b'%PDF-1.4','application/pdf'))
        assert client.post('/api/documents/upload',files=[f,f]).status_code==413
        big = client.post('/api/documents/upload',files={'files':('large.pdf',b'%PDF-1.4'+b'x'*1024*1024,'application/pdf')})
        assert big.json()['documents'][0]['status']=='rejected'
        assert list((settings.storage/'uploads').iterdir())==[]
