"""Пресеты OpenAI-совместимых провайдеров (REQ-008) и защита base_url от SSRF."""

import ipaddress
from dataclasses import dataclass
from urllib.parse import urlsplit


@dataclass(frozen=True)
class Preset:
    name: str
    title: str
    base_url: str
    default_model: str
    hint: str = ""


PRESETS: dict[str, Preset] = {
    "yandexgpt": Preset(
        "yandexgpt",
        "YandexGPT (работает из РФ)",
        "https://llm.api.cloud.yandex.net/v1",
        "gpt://<folder_id>/yandexgpt-lite/latest",
        "Нужны API-ключ сервисного аккаунта и folder_id из Yandex AI Studio",
    ),
    "openrouter": Preset(
        "openrouter",
        "OpenRouter (есть бесплатные модели)",
        "https://openrouter.ai/api/v1",
        "meta-llama/llama-3.3-70b-instruct:free",
        "Ключ: openrouter.ai/keys",
    ),
    "groq": Preset(
        "groq",
        "Groq (бесплатный тариф)",
        "https://api.groq.com/openai/v1",
        "llama-3.1-8b-instant",
        "Ключ: console.groq.com/keys",
    ),
    "custom": Preset("custom", "Свой OpenAI-совместимый API", "", "", "Укажи base_url, модель и ключ"),
}


def get_preset(name: str) -> Preset:
    return PRESETS[name]


def validate_base_url(url: str) -> str:
    """Разрешаем только https на публичный хост — пользователь не должен направить сервер внутрь сети."""
    parts = urlsplit((url or "").strip())
    if parts.scheme != "https" or not parts.hostname:
        raise ValueError("нужен https-адрес")
    host = parts.hostname.lower()
    if host == "localhost" or host.endswith(".localhost") or host.endswith(".internal") or "." not in host:
        raise ValueError("внутренний адрес запрещён")
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        ip = None
    if ip is not None and not ip.is_global:
        raise ValueError("внутренний адрес запрещён")
    return f"https://{parts.netloc}{parts.path.rstrip('/')}"
