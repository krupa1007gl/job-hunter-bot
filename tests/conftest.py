import pytest

from app.core.types import OfferData


@pytest.fixture
def offer():
    return OfferData(
        source="habr",
        external_id="123",
        url="https://career.habr.com/vacancies/123",
        title="Python-разработчик (стажёр)",
        company="ООО «Пример»",
        description="Ищем стажёра: Python, FastAPI, PostgreSQL. Можно удалённо.",
        salary_from=60000,
        salary_to=80000,
        currency="RUR",
        city="Москва",
        remote=True,
    )


@pytest.fixture
def profile():
    return {
        "search_types": ["internship", "job"],
        "stack": ["python", "fastapi"],
        "level": "intern",
        "work_format": "any",
        "city": "Москва",
        "min_salary": None,
        "about": "Студент, пишу бэкенд на Python.",
    }


@pytest.fixture
def filters():
    return {
        "keywords": ["python"],
        "stop_words": ["senior", "1с"],
        "min_salary": None,
        "work_format": "any",
        "city": None,
        "threshold": 60,
    }
