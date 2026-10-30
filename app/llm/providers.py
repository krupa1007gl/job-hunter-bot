"""Пресеты OpenAI-совместимых провайдеров (REQ-008) и защита base_url от SSRF."""

import asyncio
import ipaddress
import socket
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


TRUSTED_HOSTS = {p.base_url.split("/")[2] for p in PRESETS.values() if p.base_url}


async def ensure_public_host(base_url: str) -> None:
    """R-02: имя «своего» провайдера не должно резолвиться во внутреннюю сеть.

    Если имя не резолвится, запрос всё равно не уйдёт. Остаточный риск — DNS rebinding между
    проверкой и запросом; для v1 принят (см. REVIEW), закрывается пиннингом IP в v1.1.
    """
    host = base_url.split("/")[2].split(":")[0].lower()
    if host in TRUSTED_HOSTS:
        return
    loop = asyncio.get_running_loop()
    try:
        infos = await loop.run_in_executor(None, socket.getaddrinfo, host, 443)
    except OSError:
        return
    for info in infos:
        addr = info[4][0].split("%")[0]
        if not ipaddress.ip_address(addr).is_global:
            raise ValueError("адрес провайдера ведёт во внутреннюю сеть")
