"""REQ-017: гигиена репозитория — секретов нет, .env не коммитится, БД наружу не торчит."""

import re
import subprocess
from pathlib import Path

SECRETS = {"BOT_TOKEN", "MASTER_KEY", "POSTGRES_PASSWORD", "HH_TOKEN", "INTERNAL_TOKEN"}


def test_env_example_has_no_secret_values():
    values = dict(
        line.split("=", 1) for line in Path(".env.example").read_text(encoding="utf-8").splitlines()
        if line and not line.startswith("#")
    )
    assert SECRETS <= set(values)
    assert all(values[k] == "" for k in SECRETS)


def test_env_ignored():
    assert ".env" in Path(".gitignore").read_text(encoding="utf-8").split()


def test_db_port_not_published():
    compose = Path("docker-compose.yml").read_text(encoding="utf-8")
    db_block = compose.split("  bot:")[0]
    assert "ports:" not in db_block


def test_no_secret_like_strings_in_tracked_files():
    files = subprocess.run(["git", "ls-files"], capture_output=True, text=True, check=True).stdout.split()
    pattern = re.compile(r"\d{8,10}:[A-Za-z0-9_-]{35}|sk-[A-Za-z0-9]{32,}|gsk_[A-Za-z0-9]{40,}")
    leaks = [f for f in files if Path(f).is_file() and pattern.search(Path(f).read_text(errors="ignore"))]
    assert leaks == []
