from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    GREEN_API_ID: str
    GREEN_API_TOKEN: str
    GEMINI_API_KEY: str
    MONGO_URI: str
    MONGO_TEST_URI: str | None = None
    ADMIN_PASSWORD: str | None = None
    CLOUDINARY_CLOUD_NAME: str
    CLOUDINARY_API_KEY: str
    CLOUDINARY_API_SECRET: str
    AI_MODELS: list[str] = ["gemini-2.5-flash-lite", "gemini-2.5-flash", "gemini-1.5-flash"]
    TIMEZONE: str = "Asia/Jerusalem"
    
    # New Configs
    PROJECT_NAME: str = "Fixi Bot Server"
    BACKEND_CORS_ORIGINS: list[str] = ["*"]
    MAX_CHAT_HISTORY: int = 20
    
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

settings = Settings()