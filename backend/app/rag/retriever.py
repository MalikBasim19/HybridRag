from qdrant_client import models
from langchain_classic.retrievers import EnsembleRetriever

def hybrid(vectors, bm25, document_ids):
    published = models.Filter(must=[models.FieldCondition(
        key="metadata.document_id", match=models.MatchAny(any=document_ids))])
    return EnsembleRetriever(retrievers=[vectors.store.as_retriever(search_kwargs={'k': 5, 'filter': published}), bm25],
        weights=[0.5, 0.5], id_key='chunk_id')
