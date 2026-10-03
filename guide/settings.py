from pathlib import Path

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from guide.contracts import Selection

ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="GUIDE_", env_file=ROOT / ".env", extra="ignore")
    database_url: str = "postgresql+psycopg://localhost/scu_guide"
    allowed_origins: list[str] = ["http://127.0.0.1:8100", "http://localhost:8100"]
    dev_provider_selection: bool = True
    production_selection: Selection = Selection()
    ice_servers: list[str] = []
    budget_id: str = "mvp-initial"
    budget_usd: float = Field(default=8, gt=0, le=50)
    max_call_seconds: int = Field(default=180, ge=30, le=180)
    max_concurrent_calls: int = Field(default=2, ge=1, le=10)
    idle_seconds: int = Field(default=30, ge=10, le=120)
    research_seconds: int = Field(default=20, ge=5, le=30)
    provider_timeout_seconds: int = Field(default=10, ge=1, le=15)
    provider_call_reserve_usd: float = Field(default=0.06, gt=0, le=1)
    audio_call_reserve_usd: float = Field(default=1, ge=1, le=5)
    admin_token: SecretStr = SecretStr("")
    openai_api_key: SecretStr = Field(default=SecretStr(""), validation_alias="OPENAI_API_KEY")
    gemini_api_key: SecretStr = Field(default=SecretStr(""), validation_alias="GEMINI_API_KEY")
    anthropic_api_key: SecretStr = Field(
        default=SecretStr(""), validation_alias="ANTHROPIC_API_KEY"
    )
    deepgram_api_key: SecretStr = Field(default=SecretStr(""), validation_alias="DEEPGRAM_API_KEY")
    elevenlabs_api_key: SecretStr = Field(
        default=SecretStr(""), validation_alias="ELEVENLABS_API_KEY"
    )
    tavily_api_key: SecretStr = Field(default=SecretStr(""), validation_alias="TAVILY_API_KEY")
    elevenlabs_voice_id: str = Field(default="", validation_alias="ELEVENLABS_VOICE_ID")

    @classmethod
    def settings_customise_sources(
        cls, settings_cls, init_settings, env_settings, dotenv_settings, file_secret_settings
    ):
        def local_credentials():
            return {
                key: value
                for key, value in dotenv_settings().items()
                if value and (key.endswith("_API_KEY") or key == "ELEVENLABS_VOICE_ID")
            }

        return init_settings, local_credentials, env_settings, dotenv_settings, file_secret_settings

    @model_validator(mode="after")
    def require_postgres(self):
        if not self.database_url.startswith("postgresql+psycopg://"):
            raise ValueError("PostgreSQL with the psycopg driver is required")
        return self

    def key(self, provider: str) -> str:
        value = getattr(self, f"{provider}_api_key", SecretStr(""))
        return value.get_secret_value()
