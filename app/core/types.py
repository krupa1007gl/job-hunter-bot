from dataclasses import dataclass, field
from datetime import datetime


@dataclass(frozen=True)
class OfferData:
    """Предложение (вакансия или заказ) в виде, не зависящем от источника и БД."""

    source: str
    external_id: str
    url: str
    title: str
    company: str | None = None
    description: str = ""
    salary_from: int | None = None
    salary_to: int | None = None
    currency: str | None = None
    city: str | None = None
    remote: bool | None = None
    published_at: datetime | None = None
    extra: dict = field(default_factory=dict, compare=False)
