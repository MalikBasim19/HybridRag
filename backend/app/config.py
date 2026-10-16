from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict
BASE = Path(__file__).resolve().parents[1]
class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=BASE / '.env', extra='ignore')
    openai_api_key: str = ''
    data_dir: Path = Path('storage')
    collection: str = 'docling_pdf_collection'
    frontend_origin: str = 'http://localhost:5173'
    max_file_mb: int = 50
    max_batch_files: int = 80
    qdrant_url: str = ''
    max_context_chars: int = 60000
    @property
    def storage(self):
        return self.data_dir if self.data_dir.is_absolute() else BASE / self.data_dir
