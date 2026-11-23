from dataclasses import dataclass
from datetime import date
from typing import Literal

from app.core.db import load_env_file, read_setting
from app.core.security import AuthSettings

DEFAULT_SANDBOX_BUSINESS_DATE = date(2026, 11, 21)
SUPPORTED_LLM_PROVIDERS = ("dashscope", "openai_relay")
StructuredOutputMode = Literal["function_calling", "json_schema"]


def _normalize_structured_mode(
    value: str | None, fallback: StructuredOutputMode = "function_calling"
) -> StructuredOutputMode:
    normalized = (value or "").strip().lower()
    if normalized == "json_schema":
        return "json_schema"
    if normalized == "function_calling":
        return "function_calling"
    return fallback


@dataclass(frozen=True)
class LLMChannelSettings:
    provider: str
    base_url: str
    api_key: str
    default_model: str
    high_capability_model: str | None = None
    structured_output_mode: StructuredOutputMode = "function_calling"


@dataclass(frozen=True)
class AppSettings:
    database_url: str | None
    redis_url: str | None
    rabbitmq_url: str | None
    auth_secret: str | None
    auth_cookie_secure: str | None
    llm_provider: str
    llm_channels: dict[str, LLMChannelSettings]
    sandbox_business_date: date | None

    @classmethod
    def load(cls) -> "AppSettings":
        # 启动时一次性读取 .env 文件，避免请求期同步磁盘 I/O 阻塞事件循环
        file_values = load_env_file()
        get = lambda name: read_setting(name, file_values)  # noqa: E731

        provider = (get("LLM_PROVIDER") or "dashscope").strip().lower()
        global_mode = _normalize_structured_mode(get("LLM_STRUCTURED_OUTPUT_MODE"))

        channels: dict[str, LLMChannelSettings] = {}
        for channel_name in SUPPORTED_LLM_PROVIDERS:
            prefix = channel_name.upper()
            base_url = get(f"{prefix}_BASE_URL")
            api_key = get(f"{prefix}_API_KEY")
            default_model = get(f"{prefix}_DEFAULT_MODEL")
            if base_url and api_key and default_model:
                channel_mode = _normalize_structured_mode(
                    get(f"{prefix}_STRUCTURED_OUTPUT_MODE"), fallback=global_mode
                )
                channels[channel_name] = LLMChannelSettings(
                    provider=channel_name,
                    base_url=base_url,
                    api_key=api_key,
                    default_model=default_model,
                    high_capability_model=get(f"{prefix}_HIGH_CAPABILITY_MODEL") or None,
                    structured_output_mode=channel_mode,
                )

        raw_business_date = (get("SANDBOX_BUSINESS_DATE") or "").strip()
        if raw_business_date.lower() in {"live", "realtime", "now"}:
            sandbox_business_date: date | None = None
        elif raw_business_date:
            sandbox_business_date = date.fromisoformat(raw_business_date)
        else:
            sandbox_business_date = DEFAULT_SANDBOX_BUSINESS_DATE

        return cls(
            database_url=get("DATABASE_URL"),
            redis_url=get("REDIS_URL"),
            rabbitmq_url=get("RABBITMQ_URL"),
            auth_secret=get("AUTH_JWT_SECRET"),
            auth_cookie_secure=get("AUTH_COOKIE_SECURE"),
            llm_provider=provider,
            llm_channels=channels,
            sandbox_business_date=sandbox_business_date,
        )

    def require_database_url(self) -> str:
        if not self.database_url:
            raise RuntimeError("DATABASE_URL must be set in the environment or the project .env")
        return self.database_url

    def require_auth(self) -> AuthSettings:
        return AuthSettings.from_values(self.auth_secret, self.auth_cookie_secure)

    def active_llm_channel(self) -> LLMChannelSettings | None:
        if self.llm_provider not in SUPPORTED_LLM_PROVIDERS:
            return None
        return self.llm_channels.get(self.llm_provider)
