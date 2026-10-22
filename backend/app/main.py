import hashlib
import json
import logging
import os
from uuid import uuid4
from contextlib import asynccontextmanager
from fastapi import FastAPI, UploadFile, File, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from .config import Settings
from .service import Service
from .rag.vector_store import Vectors

class Question(BaseModel):
    question: str = Field(min_length=1, max_length=8000)

def create_app(settings=None, factory=None):
    settings = settings or Settings()
    @asynccontextmanager
    async def lifespan(app):
        (settings.storage / 'uploads').mkdir(parents=True, exist_ok=True)
        app.state.service = factory(settings) if factory else Service(settings, Vectors(settings))
        try:
            yield
        finally:
            app.state.service.close()
    app = FastAPI(title='Advanced Hybrid RAG', lifespan=lifespan)
    app.add_middleware(CORSMiddleware, allow_origins=[settings.frontend_origin], allow_methods=['GET','POST','DELETE'], allow_headers=['Content-Type'])
    @app.get('/health')
    def health(request: Request):
        s = request.app.state.service
        with s.lock:
            indexed = sum(d['status']=='indexed' for d in s.repo.list())
        return {'status': 'ok', 'openai_configured': bool(settings.openai_api_key), 'indexed_documents': indexed}
    @app.get('/api/documents')
    def documents(request: Request):
        s = request.app.state.service
        with s.lock:
            return s.repo.list()
    @app.post('/api/documents/upload', status_code=202)
    async def upload(request: Request, files: list[UploadFile] = File(...)):
        s = request.app.state.service
        if not settings.openai_api_key:
            raise HTTPException(503, 'Set OPENAI_API_KEY in backend/.env before uploading')
        if len(files) > settings.max_batch_files:
            raise HTTPException(413, f'Maximum {settings.max_batch_files} files per batch')
        results = []
        for file in files:
            filename = (file.filename or 'document.pdf').replace('\\','/').rsplit('/',1)[-1][:255]
            temp = settings.storage / 'uploads' / (str(uuid4())+'.tmp')
            try:
                if not filename.lower().endswith('.pdf'):
                    raise ValueError('Only PDF files are accepted')
                size, digest, header = 0, hashlib.sha256(), b''
                with temp.open('wb') as output:
                    while data := await file.read(1024*1024):
                        if not header:
                            header = data[:1024]
                        size += len(data)
                        if size > settings.max_file_mb * 1024*1024:
                            raise ValueError(f'PDF exceeds {settings.max_file_mb} MB limit')
                        digest.update(data)
                        output.write(data)
                if b'%PDF-' not in header:
                    raise ValueError('Invalid PDF signature')
                id = digest.hexdigest()
                with s.lock:
                    existing = s.repo.get(id)
                    if existing:
                        results.append(dict(existing, duplicate=True, uploaded_filename=filename))
                        continue
                    os.replace(temp, settings.storage / 'uploads' / (id+'.pdf'))
                    s.repo.reserve(id, filename)
                    results.append(dict(s.repo.get(id), duplicate=False))
                    s.submit(id)
            except ValueError as exc:
                results.append({'filename': filename, 'status': 'rejected', 'error': str(exc)})
            except Exception:
                logging.exception('Upload failed')
                results.append({'filename': filename, 'status': 'rejected', 'error': 'Upload could not be stored; see backend logs'})
            finally:
                temp.unlink(missing_ok=True)
                await file.close()
        return {'documents': results}
    @app.delete('/api/documents/{id}', status_code=204)
    def delete(id: str, request: Request):
        try:
            request.app.state.service.delete(id)
        except KeyError:
            raise HTTPException(404, 'Document not found')
        except ValueError as exc:
            raise HTTPException(409, str(exc))
        except Exception:
            logging.exception('Deletion failed')
            raise HTTPException(502, 'Deletion could not finish; restart backend to retry cleanup')
    @app.post('/api/chat/stream')
    def stream_chat(body: Question, request: Request):
        if not body.question.strip():
            raise HTTPException(422, 'Question cannot be blank')
        if not settings.openai_api_key:
            raise HTTPException(503, 'Set OPENAI_API_KEY in backend/.env')
        try:
            events = request.app.state.service.stream_chat(body.question.strip())
        except ValueError as exc:
            raise HTTPException(409, str(exc))
        except Exception:
            logging.exception('Retrieval failed')
            raise HTTPException(502, 'Retrieval failed; see backend logs')
        def stream():
            try:
                for event in events:
                    yield f'data: {json.dumps(event)}\n\n'
            except Exception:
                logging.exception('Streaming generation failed')
                yield f'data: {json.dumps({"type": "error", "message": "Answer interrupted; please retry. See backend logs."})}\n\n'
            finally:
                events.close()
        return StreamingResponse(stream(), media_type='text/event-stream',
            headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})
    @app.post('/api/chat')
    def chat(body: Question, request: Request):
        if not body.question.strip():
            raise HTTPException(422, 'Question cannot be blank')
        if not settings.openai_api_key:
            raise HTTPException(503, 'Set OPENAI_API_KEY in backend/.env')
        try:
            return request.app.state.service.chat(body.question.strip())
        except ValueError as exc:
            raise HTTPException(409, str(exc))
        except Exception:
            logging.exception('Chat failed')
            raise HTTPException(502, 'Retrieval or generation failed; see backend logs')
    return app

app = create_app()
