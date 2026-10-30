"""hh.ru: только официальный API и только при наличии токена приложения (REQ-018)."""

from datetime import datetime

import httpx

from ..core.types import OfferData
from .common import SourceError, strip_html

__all__ = ["SourceError", "HHClient", "parse_vacancies"]

API = "https://api.hh.ru/vacancies"


def _dt(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.strptime(s, "%Y-%m-%dT%H:%M:%S%z")
    except ValueError:
        return None


def parse_vacancies(data: dict) -> list[OfferData]:
    out: list[OfferData] = []
    for v in data.get("items") or []:
        salary = v.get("salary") or {}
        snippet = v.get("snippet") or {}
        schedule = (v.get("schedule") or {}).get("id")
        desc = "\n".join(strip_html(snippet.get(k)) for k in ("requirement", "responsibility") if snippet.get(k))
        out.append(
            OfferData(
                source="hh",
                external_id=str(v.get("id")),
                url=v.get("alternate_url") or f"https://hh.ru/vacancy/{v.get('id')}",
                title=v.get("name") or "",
                company=(v.get("employer") or {}).get("name"),
                description=desc,
                salary_from=salary.get("from"),
                salary_to=salary.get("to"),
                currency=salary.get("currency"),
                city=(v.get("area") or {}).get("name"),
                remote=True if schedule == "remote" else (None if schedule is None else False),
                published_at=_dt(v.get("published_at")),
            )
        )
    return out


class HHClient:
    def __init__(self, http: httpx.AsyncClient, token: str, user_agent: str) -> None:
        self.http, self._token, self.user_agent = http, token, user_agent

    @property
    def enabled(self) -> bool:
        return bool(self._token)

    def __repr__(self) -> str:
        return f"HHClient(enabled={self.enabled})"

    async def search(self, text: str, per_page: int = 50) -> list[OfferData]:
        if not self.enabled:
            return []
        try:
            r = await self.http.get(
                API,
                params={"text": text, "per_page": per_page, "order_by": "publication_time", "period": 3},
                headers={"Authorization": f"Bearer {self._token}", "HH-User-Agent": self.user_agent},
                timeout=30,
            )
        except httpx.HTTPError as e:
            raise SourceError(f"hh: {type(e).__name__}") from None
        if r.status_code != 200:
            raise SourceError(f"hh: HTTP {r.status_code}")
        return parse_vacancies(r.json())
