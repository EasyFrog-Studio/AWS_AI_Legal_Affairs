"""resolve_api_key() 三條路徑:注入 environ/env_file,不觸碰真實 AWS 或檔案系統之外的東西。"""
from pathlib import Path

import pytest

from ecs_fargate import resolve_api_key


def test_environ_value_wins_when_present(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("API_KEY=from-file\n", encoding="utf-8")
    assert resolve_api_key({"API_KEY": "from-environ"}, env_file) == "from-environ"


def test_falls_back_to_env_file_when_environ_missing(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "# comment line\nAI_PROVIDER=aws  # inline note\nAPI_KEY=from-file\n", encoding="utf-8"
    )
    assert resolve_api_key({}, env_file) == "from-file"


def test_raises_system_exit_when_neither_environ_nor_env_file_has_it(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("AI_PROVIDER=aws\n", encoding="utf-8")
    with pytest.raises(SystemExit):
        resolve_api_key({}, env_file)


def test_raises_system_exit_when_env_file_missing():
    with pytest.raises(SystemExit):
        resolve_api_key({}, Path("/nonexistent/.env"))
