"""Live API smoke check. Complete stages 1, 3, then 80 with real PDFs."""
import argparse
from contextlib import ExitStack
from pathlib import Path
import time
import httpx

p = argparse.ArgumentParser()
p.add_argument('pdfs', nargs='+', type=Path)
p.add_argument('--question', required=True)
p.add_argument('--url', default='http://localhost:8000')
p.add_argument('--timeout', type=int, default=7200)
a = p.parse_args()
if len(a.pdfs) not in (1, 3, 80):
    p.error('Use stages of exactly 1, 3, or 80 PDFs')
with httpx.Client(base_url=a.url, timeout=120) as client, ExitStack() as stack:
    files = [('files',(f.name,stack.enter_context(f.open('rb')),'application/pdf')) for f in a.pdfs]
    r = client.post('/api/documents/upload', files=files)
    r.raise_for_status()
    uploaded = r.json()['documents']
    print(uploaded)
    if any('id' not in d for d in uploaded):
        raise SystemExit('One or more files were rejected')
    ids = {d['id'] for d in uploaded}
    deadline = time.monotonic()+a.timeout
    while True:
        r = client.get('/api/documents'); r.raise_for_status()
        docs = [d for d in r.json() if d['id'] in ids]
        if len(docs)==len(ids) and all(d['status'] in ('indexed','failed') for d in docs):
            break
        if time.monotonic()>deadline:
            raise SystemExit('Timed out waiting for ingestion')
        time.sleep(2)
    if any(d['status']!='indexed' for d in docs):
        raise SystemExit(f'Ingestion failure: {docs}')
    r = client.post('/api/chat',json={'question':a.question});r.raise_for_status()
    print(r.json())
