# Advanced Hybrid RAG

A local React/Vite workspace backed by FastAPI, Docling, persistent Qdrant, BM25, and OpenAI. The reference notebook is preserved in `notebooks/`; its handoff document is in `docs/`. Neither is imported or executed by the API.

## Run locally

Use Python 3.12 and Node.js 22.12 or newer. On Debian/Ubuntu, install `python3.12-venv` if virtual environment creation reports missing ensurepip.

```bash
python3 -m venv .venv
source .venv/bin/activate
# Linux/Windows CPU installation (macOS: skip this line).
pip install -r backend/requirements-cpu.txt
pip install -r backend/requirements.txt
cp backend/.env.example backend/.env
# Edit backend/.env and set OPENAI_API_KEY.
cd backend
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

In a second terminal, from the repository root:

```bash
cd frontend
npm ci
npm run dev
```

Open http://localhost:5173. Upload a PDF, wait for **indexed**, then ask a question. Expand references to inspect the exact retrieved chunks. `frontend/.env.example` documents the optional API URL; OpenAI credentials belong exclusively in `backend/.env`. Restart the backend after changing its configuration.

First Docling conversion can download layout/table/OCR models and take several minutes. Internet access is required for those downloads and OpenAI calls. Large whole tables can exceed embedding token limits; that PDF fails independently without removing other documents. Error details remain in backend logs.

## Project

- `backend/app/rag/`: extraction, chunking, embeddings, vector storage, BM25, hybrid retrieval, original prompt, generation.
- `backend/app/repository.py`: SQLite document manifest and durable chunk corpus.
- `backend/app/service.py`: sequential background ingestion, publication, recovery, deletion, chat.
- `backend/app/main.py`: API, validation, lifecycle and local CORS.
- `frontend/src/`: React chat, PDF library, upload statuses, source references.
- `backend/tests/`: offline integration tests using real persistent Qdrant and BM25.

## Behavior and intentional changes

The original split settings are unchanged: each Docling text item uses 1,500 characters, 200 overlap, and separators `\n\n`, `\n`, `. `, space, empty string. Markdown tables stay whole. Semantic and BM25 each retrieve five chunks; LangChain `EnsembleRetriever` fuses ranks with weights `[0.5, 0.5]`. This is rank fusion, not an average of raw vector/BM25 scores. The embedding model remains `text-embedding-3-small`; generation remains `gpt-4.1-mini`, temperature zero.

The notebook's final QA prompt is copied verbatim into `rag/prompts.py`. An explicit retrieve → stuff → generate call replaces legacy `RetrievalQA`; it sends all selected chunks in one prompt and returns those same chunks as references. A system instruction treats uploaded text as untrusted context. This is a defense against prompt injection, not a guarantee against all malicious documents.

[Chunk IDs drive ensemble deduplication](https://reference.langchain.com/python/langchain-classic/retrievers/ensemble/EnsembleRetriever) so identical text in different documents retains its attribution. Source headers now expose filename, document ID, and Docling provenance pages when available. Page lists identify the originating Docling item; a split text chunk may inherit a multi-page item's complete list, not an exact line-to-page mapping. Missing provenance produces no page claim. References are retrieved evidence, not independently verified sentence-level citations.

The default 60,000-character context budget skips complete chunks that do not fit and reports `context_limited`; tables are never silently truncated. An overlong table has a warning in stored metadata. If no chunk fits, chat returns an actionable error. Character budgeting bounds size but is not an exact token count.

## Persistence and failure handling

The default `backend/storage/` contains hash-named uploads, `corpus.sqlite3`, and local Qdrant files. Back up the entire directory while the API is stopped. Run **one Uvicorn worker and one API instance**; the SQLite manifest and in-process job executor are intentionally local. Do not use `--workers` or run a second process against the same storage. Model and collection configuration must remain consistent with existing vectors.

SHA-256 of PDF bytes is its document ID. Uploading identical bytes returns the existing record, including its original filename, without another embedding call. Different bytes with the same filename are separate documents. A failed PDF must be deleted before retrying.

A single background executor processes files sequentially to bound Docling memory. Jobs have durable `queued`, `extracting`, `indexing`, `indexed`, `failed`, or `deleting` states. Embeddings run outside the shared publication lock; bounded Qdrant writes and SQLite publication are protected. Semantic searches filter to published document IDs so partially ingested or failed files cannot enter answers. SQLite commits chunks and indexed state together. BM25 is reconstructed once at startup and after corpus mutations, never per question. Shutdown waits for queued work; use process termination if necessary, then restart recovery marks interrupted files failed and cleans their vectors. Pending deletions finish at startup.

A crash between Qdrant writes and SQLite commit leaves no visible partial document; startup removes those vectors. Indexed PDFs remain untouched. Failed cleanup is logged and retried at startup; IDs without indexed state are excluded from queries. Deleting an indexed PDF removes its vectors, corpus and uploaded file. Already displayed chat answers remain in browser memory until refresh.

Set `QDRANT_URL=http://localhost:6333` to use a persistent Qdrant server instead. Its volume must be configured separately. This alone does not make the SQLite manifest/job executor safe for multiple API instances. For larger production workloads, add a durable job queue, distributed consistency controls, authentication, TLS, rate limits, upload sandboxing and server storage monitoring.

## API

| Method | Path | Behavior |
|---|---|---|
| GET | `/health` | API running, key configured, indexed count |
| POST | `/api/documents/upload` | Multipart `files`; returns 202 with per-file states/errors |
| GET | `/api/documents` | IDs, filenames, states, errors, chunk counts |
| DELETE | `/api/documents/{id}` | Delete a terminal-state document; busy documents return 409 |
| POST | `/api/chat` | JSON `{"question":"..."}`; answer, source objects, context limit flag |
| POST | `/api/chat/stream` | Same question JSON; SSE events: `sources`, `token` (text), `done`, or `error` (message) |

The React chat uses streaming: retrieval completes first, then model text appears as it arrives. References accompany the answer. An interrupted stream keeps partial text and shows a retry error. The original JSON chat endpoint remains available. Streaming responses disable proxy buffering via `X-Accel-Buffering: no`; any additional reverse proxy must also allow streaming without buffering.

Interactive API documentation: http://localhost:8000/docs. Health does not validate the key with OpenAI. The default limits are 80 files per batch, 50 MB per file, and 8,000 question characters. Starlette parses multipart bodies before endpoint validation, so these limits are local application checks, not an upstream denial-of-service boundary.

## Verification, in order

```bash
cd backend
../.venv/bin/python -m pytest -q tests/test_pipeline.py -k 'staged and 1'
../.venv/bin/python -m pytest -q tests/test_pipeline.py -k 'staged and 3'
../.venv/bin/python -m pytest -q tests/test_pipeline.py -k 'staged and 80'
../.venv/bin/python -m pytest -q
cd ../frontend
npm run build
```

The staged tests cover the actual HTTP endpoints, Qdrant persistence/retrieval, BM25 fusion, same-name attribution, duplicate prevention, restart, deletion, empty-corpus handling, failure isolation and interrupted-ingestion cleanup. **They use synthetic PDF payloads, an injected extractor, deterministic local embeddings and an injected generator; they do not prove Docling quality or live OpenAI correctness.**

Before relying on a corpus, complete these live acceptance stages in order:

1. Upload one real PDF through React. Ask a question with a known answer; inspect supporting text, tables, units and any provenance pages. Restart the backend and repeat.
2. Upload three different real PDFs (include two with the same filename). Ask document-specific questions and one cross-document question; verify document IDs and supporting text. Re-upload a duplicate and confirm document/chunk counts do not grow. Include an invalid PDF and confirm existing answers still work.
3. Only after those pass, upload a representative 80-PDF batch. Watch individual states, check resource usage and latency, verify questions across early/late documents, restart, and repeat the known-answer questions. Eighty small synthetic fixtures are not evidence of capacity for eighty large scanned PDFs.

Use `backend/scripts/acceptance.py` for repeatable live API smoke checks; it accepts exactly 1, 3, or 80 paths and requires an explicit question. It incurs normal OpenAI embedding/generation charges. Automated checks cannot determine answer correctness: compare output with your PDFs.

Reindexing is explicit: stop the API, back up storage, choose a new empty `DATA_DIR` and, for server Qdrant, a new collection name, then restart and re-upload PDFs. Never change embedding dimensions on an existing collection.

## Implementation verification record

The React production build passed with Node.js 22.20.0 and Vite 7.3.7. The staged offline integration runs passed for 1, 3 and 80 documents. The already-running full regression suite completed with 8 passing tests. Live PDF extraction, live OpenAI answers and interactive browser checks were not verified; further testing was skipped at the user's request.

The integration environment resolved FastAPI 0.143.2, Qdrant client 1.19.1, langchain-classic 1.0.8, langchain-community 0.4.2, langchain-qdrant 1.1.0 and langchain-openai 1.7.2 on Python 3.12.3. LangChain emits a deprecation warning for the community package; BM25 is retained there to preserve the requested implementation. The requirements files specify compatible version ranges; frontend/package-lock.json records exact frontend dependencies.

## CPU ingestion compatibility

Docling extraction explicitly selects `AcceleratorDevice.CPU`. The supported runtime is PyTorch 2.7.1 + torchvision 0.22.1 with Transformers 5.19.0 and Docling 2.139.0, pinned in backend requirements. PyTorch 2.6.0 CPU raises from `torch.accelerator.current_accelerator()` when no accelerator exists; Transformers calls that API while importing detection models, before Docling device selection. [PyTorch 2.7.1 defines the no-accelerator result as None](https://github.com/pytorch/pytorch/blob/v2.7.1/torch/accelerator/__init__.py), allowing Transformers to fall back to CPU. Merely hiding CUDA or selecting CPU in Docling does not repair the old import-time failure. No monkey patches or site-packages edits are used.

For an existing environment, stop the backend, activate `.venv`, and run from the project root:

```bash
python -m pip install -r backend/requirements-cpu.txt
python -m pip install -r backend/requirements.txt
cd backend
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Delete the failed PDF record in the library and upload it again. Indexed documents do not require re-embedding.

For an offline compatibility check only (no pretrained model downloads or OpenAI calls):

```bash
.venv/bin/python backend/scripts/check_cpu_runtime.py
```

This verifies the previously failing import, runs a tiny random detection model on CPU and checks Docling's CPU configuration; it does not validate real PDF conversion or pretrained model downloads. PyTorch and torchvision versions follow the [official matched CPU wheel instructions](https://pytorch.org/get-started/previous-versions/#v271).

The focused CPU check passed with torch 2.7.1+cpu, torchvision 0.22.1+cpu, Transformers 5.19.0 and Docling 2.139.0: accelerator detection returned None, the detection model completed inference on CPU, and Docling accepted explicit CPU configuration. Real PDF conversion was not run for this fix.
