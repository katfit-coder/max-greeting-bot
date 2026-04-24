from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # ВСЕ секреты — только через env (Render Environment или локальный .env).
    # Никогда не коммить реальные ключи/пароли в этот файл.
    max_bot_token: str = ""
    gigachat_auth_key: str = ""
    gigachat_scope: str = "GIGACHAT_API_B2B"
    smtp_host: str = "smtp.yandex.ru"
    smtp_port: int = 465
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from: str = ""
    public_base_url: str = ""
    database_url: str = "sqlite:///./bot.db"

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()
