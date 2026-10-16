from uuid import NAMESPACE_URL, uuid5
from langchain_text_splitters import RecursiveCharacterTextSplitter

def chunk(items):
    splitter = RecursiveCharacterTextSplitter(chunk_size=1500, chunk_overlap=200,
        separators=['\n\n', '\n', '. ', ' ', ''])
    chunks = splitter.split_documents([d for d in items if d.metadata['type'] == 'text'])
    chunks += [d for d in items if d.metadata['type'] == 'table']
    for i, doc in enumerate(chunks):
        doc.metadata['chunk_id'] = str(uuid5(NAMESPACE_URL, f"{doc.metadata['document_id']}:{i}"))
        if doc.metadata['type'] == 'table' and len(doc.page_content) > 8000:
            doc.metadata['warning'] = 'Whole table exceeds 8,000 characters'
    if not chunks:
        raise ValueError('PDF produced no searchable text or tables')
    return chunks
