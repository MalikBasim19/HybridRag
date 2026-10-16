from langchain_community.retrievers import BM25Retriever

def build_bm25(chunks):
    if not chunks:
        return None
    retriever = BM25Retriever.from_documents(chunks)
    retriever.k = 5
    return retriever
