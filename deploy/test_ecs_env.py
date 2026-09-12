"""resolve_api_key() 的取值來源,不觸碰真實 AWS。"""
import pytest

from ecs_fargate import ALLOWED_INGRESS, CLOUD_API_KEY, ENV, resolve_api_key


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


def test_task_env_carries_no_gemini_key():
    """雲端 task 不帶 Gemini 金鑰:文件型態判不出來時退回人工,不把卷證內容送第三方。"""
    assert "GEMINI_API_KEY" not in ENV


def test_every_allowed_ingress_is_a_single_host():
    """白名單只放個別主機;任何比 /32 寬的遮罩都會把暴露面擴大到整段網段。"""
    assert ALLOWED_INGRESS
    for cidr, note in ALLOWED_INGRESS:
        assert cidr.endswith("/32"), cidr
        assert note
