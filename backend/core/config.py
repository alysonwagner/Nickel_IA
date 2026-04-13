import os
from dotenv import load_dotenv
from pydantic import BaseModel

load_dotenv()

class Settings(BaseModel):
    """
    Classe central de configurações do NICKEL_IA.
    """
    # --- Supabase ---
    SUPABASE_URL: str = os.getenv("SUPABASE_URL", "")
    SUPABASE_KEY: str = os.getenv("SUPABASE_KEY", "")
    
    # --- Inteligência Artificial (Custo Zero para Dev) ---
    GEMINI_API_KEY: str = os.getenv("GEMINI_API_KEY", "")
    
    # --- Telegram Bot ---
    TELEGRAM_TOKEN: str = os.getenv("TELEGRAM_TOKEN", "")
    
    ENVIRONMENT: str = os.getenv("ENVIRONMENT", "development")

    def validate_keys(self):
        missing = []
        if not self.SUPABASE_URL: missing.append("SUPABASE_URL")
        if not self.SUPABASE_KEY: missing.append("SUPABASE_KEY")
        if not self.GEMINI_API_KEY: missing.append("GEMINI_API_KEY")
        
        if missing:
            raise ValueError(f"⚠️ ATENÇÃO: As seguintes chaves estão faltando no .env: {', '.join(missing)}")

settings = Settings()