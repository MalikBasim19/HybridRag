from langchain_openai import OpenAIEmbeddings

def create_embeddings(settings):
    return OpenAIEmbeddings(model='text-embedding-3-small', api_key=settings.openai_api_key or 'missing')
