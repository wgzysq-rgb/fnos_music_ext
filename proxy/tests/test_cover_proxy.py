# -*- coding: utf-8 -*-
"""封面代理 + NAS 落盘缓存（2.7.7）回归测试。

背景：电脑浏览器对 http 封面直链做 mixed-content 自动升级，CDN 不支持
https 时图片被静默丢弃（手机 App 不受限）。修复 = /static/cover 的全部
外部 302 点先走本地缓存/代理取回，失败才回落 302 直链。
"""
import os

import httpx
import pytest
from starlette.testclient import TestClient

from proxy import app as proxy_app
from proxy.app import (
    app,
    _cover_cache_evict,
    _cover_cache_key,
    _cover_cache_lookup,
    _cover_cache_store,
    _cover_proxy_response,
)

JPEG_BYTES = b"\xff\xd8\xff\xe0\x00\x10JFIF_PROXY" + b"\x00" * 60
COVER_URL = "http://cdn.example.com/covers/abc.jpg"


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def cover_cache_env(monkeypatch, tmp_path):
    """隔离的封面缓存目录 + 代理开关打开。"""
    monkeypatch.setitem(proxy_app.CONF, "cover_proxy", True)
    monkeypatch.setitem(proxy_app.CONF, "cover_cache_dir", str(tmp_path / "covers"))
    return tmp_path / "covers"


# ----------------------------------------------------------------- 单元层 --

def test_cover_cache_store_lookup_roundtrip(cover_cache_env):
    path = _cover_cache_store(COVER_URL, JPEG_BYTES, "image/jpeg")
    assert path and os.path.isfile(path)
    assert path.endswith(".jpg")
    # 同 URL 命中；不同 URL 不命中
    assert _cover_cache_lookup(COVER_URL) == path
    assert _cover_cache_lookup("http://cdn.example.com/covers/other.jpg") == ""
    with open(path, "rb") as f:
        assert f.read() == JPEG_BYTES


def test_cover_cache_key_stable():
    assert _cover_cache_key(COVER_URL) == _cover_cache_key(COVER_URL)
    assert _cover_cache_key(COVER_URL) != _cover_cache_key(COVER_URL + "?x=1")


def test_cover_cache_evict_oldest_first(cover_cache_env, monkeypatch):
    monkeypatch.setitem(proxy_app.CONF, "cover_cache_max", 10)
    kept = []
    for i in range(12):
        url = f"http://cdn.example.com/covers/evict-{i}.jpg"
        path = _cover_cache_store(url, JPEG_BYTES, "image/jpeg")
        # 显式设定 mtime，保证淘汰顺序确定（i 越大越新）
        os.utime(path, (1000 + i, 1000 + i))
        if i >= 2:  # 最旧的 0、1 被淘汰
            kept.append(path)
    files = [os.path.join(cover_cache_env, n) for n in os.listdir(cover_cache_env)]
    assert len(files) == 10
    assert set(files) == set(kept)


@pytest.mark.anyio
async def test_cover_proxy_response_disabled_returns_none(monkeypatch, cover_cache_env):
    monkeypatch.setitem(proxy_app.CONF, "cover_proxy", False)
    assert await _cover_proxy_response(COVER_URL) is None


@pytest.mark.anyio
async def test_cover_proxy_response_fetch_failure_returns_none(monkeypatch, cover_cache_env):
    monkeypatch.setattr(proxy_app, "download_cover_bytes", lambda url: None)
    assert await _cover_proxy_response(COVER_URL) is None


@pytest.mark.anyio
async def test_cover_proxy_response_serves_from_cache(cover_cache_env, monkeypatch):
    _cover_cache_store(COVER_URL, JPEG_BYTES, "image/jpeg")

    def _boom(url):
        raise AssertionError("cache hit must not hit network")

    monkeypatch.setattr(proxy_app, "download_cover_bytes", _boom)
    resp = await _cover_proxy_response(COVER_URL)
    assert resp is not None
    assert "max-age=86400" in resp.headers.get("cache-control", "")


@pytest.mark.anyio
async def test_cover_proxy_response_fetch_and_store(monkeypatch, cover_cache_env):
    monkeypatch.setattr(proxy_app, "download_cover_bytes", lambda url: (JPEG_BYTES, "image/jpeg"))
    resp = await _cover_proxy_response(COVER_URL)
    assert resp is not None
    assert _cover_cache_lookup(COVER_URL), "fetch success must persist to cache"


@pytest.mark.anyio
async def test_cover_proxy_response_rejects_non_http_and_kw_text(monkeypatch, cover_cache_env):
    monkeypatch.setattr(
        proxy_app, "download_cover_bytes", lambda url: (_ for _ in ()).throw(AssertionError())
    )
    assert await _cover_proxy_response("") is None
    assert await _cover_proxy_response("ftp://x/y.jpg") is None
    kw_text = f"http://{proxy_app._KW_TEXT_COVER_HOST}/120.jpg"
    assert await _cover_proxy_response(kw_text) is None


# ----------------------------------------------------------------- 接口层 --

def _online_guid_stub(monkeypatch, guid, cover_url):
    async def _fake_online_info(request, g, include_lyric=True):
        return {"cover_url": cover_url, "title": "t", "artist": "a"} if g == guid else None

    monkeypatch.setattr(proxy_app, "_online_info", _fake_online_info)


def _no_upstream_client():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="should not reach upstream")

    app.state.upstream_client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="http://unix"
    )


GUID = "online:lx:wy:1415111009"


def test_static_cover_online_direct_url_proxied(monkeypatch, cover_cache_env):
    """在线曲目直链封面：首次代理取回 200 + 落盘，二次纯缓存命中（不再联网）。"""
    _online_guid_stub(monkeypatch, GUID, COVER_URL)
    _no_upstream_client()
    monkeypatch.setattr(proxy_app, "download_cover_bytes", lambda url: (JPEG_BYTES, "image/jpeg"))

    with TestClient(app) as client:
        resp = client.get(f"/music/api/v1/static/cover/{GUID}", follow_redirects=False)
        assert resp.status_code == 200
        assert resp.content == JPEG_BYTES
        assert resp.headers.get("content-type") == "image/jpeg"
        assert "max-age=86400" in resp.headers.get("cache-control", "")
        assert _cover_cache_lookup(COVER_URL)

        # 二次：断网（download 抛错）仍应从缓存返回 200
        def _boom(url):
            raise AssertionError("second request must be served from cache")

        monkeypatch.setattr(proxy_app, "download_cover_bytes", _boom)
        resp2 = client.get(f"/music/api/v1/static/cover/{GUID}", follow_redirects=False)
        assert resp2.status_code == 200
        assert resp2.content == JPEG_BYTES


def test_static_cover_online_proxy_failure_falls_back_302(monkeypatch, cover_cache_env):
    """代理取回失败（CDN 不可达/非图片）：回落 302 直链，行为与旧版一致。"""
    _online_guid_stub(monkeypatch, GUID, COVER_URL)
    _no_upstream_client()
    monkeypatch.setattr(proxy_app, "download_cover_bytes", lambda url: None)

    with TestClient(app) as client:
        resp = client.get(f"/music/api/v1/static/cover/{GUID}", follow_redirects=False)
        assert resp.status_code == 302
        assert resp.headers.get("location") == COVER_URL


def test_static_cover_online_proxy_disabled_keeps_302(monkeypatch, cover_cache_env):
    """开关关闭：完全不尝试代理，直接 302（也不该有任何缓存写动作）。"""
    _online_guid_stub(monkeypatch, GUID, COVER_URL)
    _no_upstream_client()
    monkeypatch.setitem(proxy_app.CONF, "cover_proxy", False)

    def _boom(url):
        raise AssertionError("disabled proxy must not fetch")

    monkeypatch.setattr(proxy_app, "download_cover_bytes", _boom)

    with TestClient(app) as client:
        resp = client.get(f"/music/api/v1/static/cover/{GUID}", follow_redirects=False)
        assert resp.status_code == 302
        assert resp.headers.get("location") == COVER_URL
        assert not os.path.exists(cover_cache_env) or not os.listdir(cover_cache_env)
