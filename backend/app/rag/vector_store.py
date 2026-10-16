from threading import RLock
from qdrant_client import QdrantClient, models
from langchain_qdrant import QdrantVectorStore
from .embeddings import create_embeddings

class Vectors:
    def __init__(self, settings):
        self.client = QdrantClient(url=settings.qdrant_url) if settings.qdrant_url else QdrantClient(path=str(settings.storage / 'qdrant'))
        self.lock = RLock()
        self.collection = settings.collection
        if not self.client.collection_exists(self.collection):
            self.client.create_collection(self.collection, vectors_config=models.VectorParams(size=1536, distance=models.Distance.COSINE))
        self.store = QdrantVectorStore(client=self.client, collection_name=self.collection,
            embedding=create_embeddings(settings), validate_embeddings=False, validate_collection_config=False)
    def add(self, chunks):
        # Bounded API batches; deterministic IDs permit idempotent recovery.
        for start in range(0, len(chunks), 32):
            batch = chunks[start:start+32]
            embeddings = self.store.embeddings.embed_documents([d.page_content for d in batch])
            with self.lock:
                self.client.upsert(self.collection, points=[models.PointStruct(
                    id=d.metadata['chunk_id'], vector=v, payload={'page_content': d.page_content, 'metadata': d.metadata})
                    for d, v in zip(batch, embeddings)], wait=True)
    def delete(self, document_id):
        self.client.delete(self.collection, points_selector=models.FilterSelector(filter=models.Filter(must=[
            models.FieldCondition(key='metadata.document_id', match=models.MatchValue(value=document_id))])), wait=True)
    def close(self):
        self.client.close()
