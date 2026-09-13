"""前端靜態檔的解析:缺檔要 404 而不是回 index.html,index.html 不得被快取沿用。"""
from pathlib import Path

import pytest
from fastapi import HTTPException

from app.main import resolve_static_request


@pytest.fixture
def static_dir(tmp_path):
    root = tmp_path / "static"
    (root / "assets").mkdir(parents=True)
    (root / "index.html").write_text("<!doctype html><html></html>", encoding="utf-8")
    (root / "assets" / "index-AAA111.js").write_text("console.log(1)", encoding="utf-8")
    (root / "assets" / "index-BBB222.css").write_text("body{}", encoding="utf-8")
    return root


@pytest.mark.parametrize("path", ["assets/index-AAA111.js", "assets/index-BBB222.css"])
def test_existing_asset_is_served_with_immutable_cache(path, static_dir):
    """檔名帶內容雜湊,內容一變檔名就變,可以長快取。"""
    resp = resolve_static_request(path, static_dir)
    assert Path(resp.path) == (static_dir / path).resolve()
    assert "immutable" in resp.headers["cache-control"]


@pytest.mark.parametrize("path", [
    "assets/index-OLDHASH.js",
    "assets/index-OLDHASH.css",
    "favicon.ico",
    "nested/deep/thing.woff2",
])
def test_missing_file_request_is_404_not_index(path, static_dir):
    """回 index.html 的話,瀏覽器會把 HTML 當 JS/CSS 解析——部署後舊分頁就這樣安靜地白畫面。"""
    with pytest.raises(HTTPException) as exc:
        resolve_static_request(path, static_dir)
    assert exc.value.status_code == 404


@pytest.mark.parametrize("path", ["", "login", "cases/c-92508cbc", "cases/c-1/draft"])
def test_spa_route_falls_back_to_index_without_caching(path, static_dir):
    """index.html 內嵌的資產雜湊每次部署都變;讓瀏覽器沿用舊的就是在指向已不存在的檔案。"""
    resp = resolve_static_request(path, static_dir)
    assert Path(resp.path) == static_dir / "index.html"
    assert resp.headers["cache-control"] == "no-cache"


@pytest.mark.parametrize("path", ["../secret.txt", "assets/../../secret.txt", "..%2fsecret.txt"])
def test_path_traversal_never_escapes_static_root(path, static_dir, tmp_path):
    (tmp_path / "secret.txt").write_text("private", encoding="utf-8")
    try:
        resp = resolve_static_request(path, static_dir)
    except HTTPException as exc:
        assert exc.value.status_code == 404 if hasattr(exc, "value") else exc.status_code == 404
        return
    assert Path(resp.path) == static_dir / "index.html"


def test_missing_index_html_is_404(tmp_path):
    """前端沒建置時不該假裝有頁面。"""
    empty = tmp_path / "static"
    empty.mkdir()
    with pytest.raises(HTTPException) as exc:
        resolve_static_request("login", empty)
    assert exc.value.status_code == 404
