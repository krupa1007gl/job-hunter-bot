"""OpenAI-совместимый клиент chat/completions. Ключ никогда не попадает в текст ошибок."""

import httpx

from .providers import validate_base_url

USER_MESSAGES = {
    "auth": "провайдер не принял ключ — проверь ключ и права",
    "rate_limit": "провайдер ограничил частоту или исчерпан бесплатный лимит",
    "unavailable": "провайдер недоступен с нашего сервера",
    "bad_response": "провайдер вернул неожиданный ответ",
    "config": "неверный адрес провайдера",
}


class LLMError(Exception):
    def __init__(self, kind: str, status: int | None = None) -> None:
        self.kind = kind
        self.status = status
        super().__init__(f"LLMError({kind}, status={status})")

    @property
    def user_message(self) -> str:
        return USER_MESSAGES.get(self.kind, "ошибка AI-провайдера")


async def chat(
    http: httpx.AsyncClient,
    base_url: str,
    model: str,
    api_key: str,
    messages: list[dict],
    timeout: float = 30.0,
    max_tokens: int = 700,
) -> str:
    try:
        base = validate_base_url(base_url)
    except ValueError:
        raise LLMError("config") from None
    try:
        r = await http.post(
            f"{base}/chat/completions",
            headers={"Authorization": f"Bearer {api_key}"},
            json={"model": model, "messages": messages, "temperature": 0.2, "max_tokens": max_tokens},
            timeout=timeout,
        )
    except httpx.HTTPError:
        raise LLMError("unavailable") from None
    if r.status_code in (401, 403):
        raise LLMError("auth", r.status_code)
    if r.status_code == 429:
        raise LLMError("rate_limit", r.status_code)
    if r.status_code >= 400:
        raise LLMError("unavailable", r.status_code)
    try:
        return str(r.json()["choices"][0]["message"]["content"])
    except (ValueError, KeyError, IndexError, TypeError):
        raise LLMError("bad_response", r.status_code) from None


async def check_key(http: httpx.AsyncClient, base_url: str, model: str, api_key: str) -> None:
    await chat(
        http,
        base_url,
        model,
        api_key,
        [{"role": "user", "content": "Ответь одним словом: ок"}],
        timeout=20.0,
        max_tokens=5,
    )
