"""Строгий разбор ответа LLM на оценку (REQ-009, REQ-012)."""

import json
import re

_OBJ = re.compile(r"\{.*?\}", re.S)


def parse_score_json(text: str) -> tuple[int, str] | None:
    if not text:
        return None
    for candidate in _OBJ.findall(text):
        try:
            data = json.loads(candidate)
        except ValueError:
            continue
        if not isinstance(data, dict) or "score" not in data or "reason" not in data:
            continue
        try:
            score = int(str(data["score"]).strip())
        except ValueError:
            return None
        if not 0 <= score <= 100:
            return None
        reason = str(data["reason"]).strip()[:200]
        return score, reason
    return None
