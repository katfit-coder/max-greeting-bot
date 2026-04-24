from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    max_bot_token: str = ""
    gigachat_auth_key: str = ""
    gigachat_scope: str = "GIGACHAT_API_PERS"
    smtp_host: str = ""
    smtp_port: int = 465
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from: str = ""
    public_base_url: str = ""
    database_url: str = "sqlite:///./bot.db"

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()
