"""resolve_api_key() 與白名單/帳號 ID 的取值來源,不觸碰真實 AWS。"""
import os
import re
from pathlib import Path

import pytest

_ENVIRON_BEFORE_IMPORT = dict(os.environ)  # 必須早於下面的匯入,用來驗證 .env 沒被灌進 os.environ

import ecs_fargate  # noqa: E402
from ecs_fargate import CLOUD_API_KEY, ENV, parse_allowed_ingress, resolve_api_key  # noqa: E402


@pytest.mark.parametrize("value", ["from-environ", "another-key"])
def test_environ_value_wins_when_present(value):
    assert resolve_api_key({"API_KEY": value}) == value


def test_falls_back_to_cloud_key_when_environ_missing():
    assert resolve_api_key({}) == CLOUD_API_KEY


def test_empty_environ_value_falls_back_to_cloud_key():
    assert resolve_api_key({"API_KEY": ""}) == CLOUD_API_KEY


def test_cloud_api_key_is_not_empty():
    """空字串會讓 auth.require_api_key 拒絕所有請求,部署出去就是沒有人能登入。"""
    assert CLOUD_API_KEY


def test_reading_env_file_does_not_pollute_environ():
    """.env 的鍵灌進 os.environ 會讓 resolve_api_key(os.environ) 撈到開發者本機那把金鑰,
    雲端就不是登入頁公告的 CLOUD_API_KEY——評審照著頁面輸入會登不進去。"""
    leaked = sorted(
        k for k in ecs_fargate.ENV_FILE_VALUES
        if k not in _ENVIRON_BEFORE_IMPORT and k in os.environ
    )
    assert not leaked, leaked


def test_task_env_carries_no_gemini_key():
    """雲端 task 不帶 Gemini 金鑰:文件型態判不出來時退回人工,不把卷證內容送第三方。"""
    assert "GEMINI_API_KEY" not in ENV


# ---- parse_allowed_ingress:白名單改由 .env 帶入 ----

@pytest.mark.parametrize("raw,expected", [
    ("198.51.100.7/32=judge-1", [("198.51.100.7/32", "judge-1")]),
    (" 203.0.113.9/32 = developer , 192.0.2.4/32=judge-2 ",
     [("203.0.113.9/32", "developer"), ("192.0.2.4/32", "judge-2")]),
])
def test_parses_cidr_and_note_pairs(raw, expected):
    assert parse_allowed_ingress(raw) == expected


@pytest.mark.parametrize("raw", ["", "   ", ",,"])
def test_empty_whitelist_is_rejected(raw):
    """空白名單會讓對帳把所有 ingress 撤光,部署完沒有人連得進去——要爆而不是靜靜放行。"""
    with pytest.raises(ValueError, match="DEPLOY_ALLOWED_INGRESS"):
        parse_allowed_ingress(raw)


@pytest.mark.parametrize("raw", ["198.51.100.0/24=judge-1", "198.51.100.7=judge-1"])
def test_mask_wider_than_single_host_is_rejected(raw):
    """比 /32 寬的遮罩會把暴露面擴大到整段網段。"""
    with pytest.raises(ValueError, match="/32"):
        parse_allowed_ingress(raw)


@pytest.mark.parametrize("raw", ["198.51.100.7/32", "198.51.100.7/32="])
def test_entry_without_note_is_rejected(raw):
    """說明會寫進 SG 規則;沒有說明,日後看不出這個洞是開給誰的。"""
    with pytest.raises(ValueError, match="CIDR=說明"):
        parse_allowed_ingress(raw)


# ---- 帳號 ID 不得回到原始碼 ----

_TWELVE_DIGITS = re.compile(r"\b\d{12}\b")
_DEPLOY_DIR = Path(__file__).resolve().parent


@pytest.mark.parametrize("relative", [
    "deploy/ecs_fargate.py",
    "deploy/push_ecr.ps1",
    "aws_setup/config.py",
])
def test_account_id_is_not_hardcoded(relative):
    """帳號 ID 只留在 .env;寫回原始碼等於又把它推上版控。"""
    source = (_DEPLOY_DIR.parent / relative).read_text(encoding="utf-8")
    assert not _TWELVE_DIGITS.search(source), relative
