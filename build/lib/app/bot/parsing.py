"""Разбор пользовательского ввода в диалогах бота."""

import re

_URL = re.compile(r"https?://[^\s<>\"']+")
MAX_SALARY = 100_000_000


def parse_list(text: str) -> list[str]:
    out: list[str] = []
    for part in re.split(r"[,;\n]+", text or ""):
        v = part.strip().lower()
        if v and v not in out:
            out.append(v)
    return out


def parse_salary(text: str) -> int | None:
    t = (text or "").strip().lower()
    if t in {"нет", "-", "—", "не важно", "0", ""}:
        return None
    digits = t.replace(" ", "").replace(" ", "")
    if not digits.isdigit():
        raise ValueError("укажи сумму числом, например 80000, или «нет»")
    value = int(digits)
    if value > MAX_SALARY:
        raise ValueError("слишком большая сумма")
    return value or None


def parse_threshold(text: str) -> int:
    t = (text or "").strip()
    if not t.isdigit() or not 0 <= int(t) <= 100:
        raise ValueError("порог — число от 0 до 100")
    return int(t)


def extract_url(text: str) -> str | None:
    m = _URL.search(text or "")
    return m.group(0).rstrip(".,)") if m else None
