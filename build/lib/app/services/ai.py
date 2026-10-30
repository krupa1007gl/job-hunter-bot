"""Сборка LLM-вызова из ключа пользователя. Ключ расшифровывается только на время запроса."""

import httpx

from ..core.crypto import KeyCipher
from ..llm import client
from ..models import AIKey
from .offers import LLMCall


def make_llm(http: httpx.AsyncClient, cipher: KeyCipher, key: AIKey | None, max_tokens: int = 300) -> LLMCall | None:
    if key is None:
        return None
    base_url, model, token = key.base_url, key.model, key.key_encrypted

    async def call(messages: list[dict]) -> str:
        return await client.chat(http, base_url, model, cipher.decrypt(token), messages, max_tokens=max_tokens)

    return call
