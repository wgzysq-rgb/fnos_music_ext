# -*- coding: utf-8 -*-
"""歌单封面出口回归测试（2.7.8）。

背景：`/static/cover` 里歌单分支（每日推荐/热门推荐）在没有"现成封面"时直接
返回 404，而在线音源构建的推荐歌单**整单都不带 cover_url、coverId 又是
online: 形态** —— `pick_playlist_cover_track()` 返回 None，歌单于是完全没有
封面。文件顶部写明的原则是"在线曲目封面永不 404"，歌单封面同理。
"""
import httpx
import pytest
from starlette.testclient import TestClient

from proxy import app as proxy_app
from proxy.app import app

DIALY_GUID = "online:playlist:daily:u1"
HOT_GUID = "online:playlist:hot:u1"
JPEG_BYTES = b"\xff\xd8\xff\xe0\x00\x10JFIF_PL" + b"\x00" * 40
COVER_URL = "http://cdn.example.com/pl.jpg"


@pytest.fixture
def anyio_backend():
    return "asyncio"


def _upstream(cover_status: int = 404):
    """上游：/user/me 已登录；官方静态封面端点返回指定状态码。"""
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/user/me"):
            return httpx.Response(200, json={"code": 0, "data": {"guid": "u1"}})
        if path == "/music/api/v1/static/cover":
            if cover_status == 200:
                return httpx.Response(200, content=b"official-img", headers={"content-type": "image/png"})
            return httpx.Response(cover_status, json={"code": 404})
        return httpx.Response(200, json={"code": 0, "data": None})

    app.state.upstream_client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="http://unix"
    )


def _stub_daily(monkeypatch, tracks, kind="daily"):
    monkeypatch.setattr(
        proxy_app.dailyrec, "load_daily_cache", lambda *a, **k: {"tracks": tracks}
    )
    monkeypatch.setattr(proxy_app.dailyrec, "today_key", lambda: "20261010")
    return DIALY_GUID if kind == "daily" else HOT_GUID


def _stub_online_chain(monkeypatch, info=None, kw_rid="", qq="", netease=""):
    """让在线封面链可控：无网络、无登记时一路走到占位图。"""
    async def _fake_online_info(request, guid, include_lyric=True):
        return info

    monkeypatch.setattr(proxy_app, "_online_info", _fake_online_info)
    monkeypatch.setattr(proxy_app, "_kw_rid_from_guid", lambda guid: kw_rid)

    async def _fake_kw(rid):
        return ""

    async def _fake_netease(request, guid, data):
        return netease

    monkeypatch.setattr(proxy_app, "_kw_cover_by_rid", _fake_kw)
    monkeypatch.setattr(proxy_app, "_qq_cover_by_albummid", lambda albummid: qq)
    monkeypatch.setattr(proxy_app, "_enrich_cover_via_netease", _fake_netease)
    monkeypatch.setattr(proxy_app, "_registry_apply", lambda guid, data: data or {})


def test_daily_playlist_all_online_without_cover_never_404(monkeypatch):
    """在线音源构建的推荐歌单（整单无 cover_url）→ 占位图，绝不 404。"""
    guid = _stub_daily(monkeypatch, [{"guid": "online:lx:wy:1415111009", "title": "t", "artist": "a"}])
    _stub_online_chain(monkeypatch, info=None)
    _upstream()

    with TestClient(app) as client:
        resp = client.get(f"/music/api/v1/static/cover/{guid}", follow_redirects=False)
    assert resp.status_code == 200
    assert resp.headers.get("content-type") == "image/png"


def test_hot_playlist_online_track_cover_from_registry_is_served(monkeypatch, tmp_path):
    """登记表里有这张歌单首曲的封面直链 → 代理取回并落盘（不再空封面）。"""
    guid = _stub_daily(monkeypatch, [{"guid": "online:lx:wy:1", "title": "t", "artist": "a"}], kind="hot")
    _upstream()
    # 在线链：上游 info 为空，但登记表能给出 cover_url
    async def _fake_online_info(request, g, include_lyric=True):
        return None

    monkeypatch.setattr(proxy_app, "_online_info", _fake_online_info)
    monkeypatch.setattr(
        proxy_app, "_registry_apply",
        lambda g, data: {"cover_url": COVER_URL} if g == "online:lx:wy:1" else (data or {}),
    )
    monkeypatch.setattr(proxy_app, "_kw_rid_from_guid", lambda g: "")
    monkeypatch.setattr(proxy_app, "_qq_cover_by_albummid", lambda albummid: "")
    monkeypatch.setitem(proxy_app.CONF, "cover_proxy", True)
    monkeypatch.setitem(proxy_app.CONF, "cover_cache_dir", str(tmp_path / "covers"))
    monkeypatch.setattr(proxy_app, "download_cover_bytes", lambda url: (JPEG_BYTES, "image/jpeg"))

    with TestClient(app) as client:
        resp = client.get(f"/music/api/v1/static/cover/{guid}", follow_redirects=False)
    assert resp.status_code == 200
    assert resp.content == JPEG_BYTES
    assert proxy_app._cover_cache_lookup(COVER_URL)


def test_daily_playlist_local_coverid_upstream_404_falls_back_placeholder(monkeypatch):
    """本地曲目的官方 coverId 在上游取不到（404）→ 占位图兜底，不留空。"""
    guid = _stub_daily(monkeypatch, [{"guid": "ltabcdef", "coverId": "c" * 32, "title": "t"}])
    _upstream(cover_status=404)

    with TestClient(app) as client:
        resp = client.get(f"/music/api/v1/static/cover/{guid}", follow_redirects=False)
    assert resp.status_code == 200
    assert resp.headers.get("content-type") == "image/png"


def test_daily_playlist_local_coverid_upstream_200_passthrough(monkeypatch):
    """上游有这张官方封面 → 原样透传（回归保护）。"""
    guid = _stub_daily(monkeypatch, [{"guid": "ltabcdef", "coverId": "c" * 32, "title": "t"}])
    _upstream(cover_status=200)

    with TestClient(app) as client:
        resp = client.get(f"/music/api/v1/static/cover/{guid}", follow_redirects=False)
    assert resp.status_code == 200
    assert resp.content == b"official-img"


def test_daily_playlist_with_cover_url_is_proxied(monkeypatch, tmp_path):
    """快照自带 cover_url（本地曲目登记过）→ 走代理/302，不到占位图。"""
    guid = _stub_daily(monkeypatch, [{"guid": "ltabcdef", "cover_url": COVER_URL, "title": "t"}])
    _upstream()
    monkeypatch.setitem(proxy_app.CONF, "cover_proxy", False)  # 关代理走纯 302 断言直链

    with TestClient(app) as client:
        resp = client.get(f"/music/api/v1/static/cover/{guid}", follow_redirects=False)
    assert resp.status_code == 302
    assert resp.headers.get("location") == COVER_URL


def test_netease_playlist_without_registered_cover_serves_placeholder(monkeypatch):
    """网易账号歌单封面直链缺失（重启后反查表未重建）→ 占位图，不是 404。"""
    monkeypatch.setattr(proxy_app.nmpl, "playlist_id_from_cover_request", lambda guid, raw: 12345)
    monkeypatch.setattr(proxy_app.nmpl, "cover_url_for", lambda pid: "")

    with TestClient(app) as client:
        resp = client.get("/music/api/v1/static/cover?coverId=track_" + "a" * 32, follow_redirects=False)
    assert resp.status_code == 200
    assert resp.headers.get("content-type") == "image/png"


@pytest.mark.anyio
async def test_cover_proxy_negative_cache_skips_repeat_fetch(monkeypatch, tmp_path):
    """取回失败的封面 URL 在 TTL 内不重复下载（避免一页封面被拖满超时）。"""
    calls = {"n": 0}

    def _fail(url):
        calls["n"] += 1
        return None

    monkeypatch.setitem(proxy_app.CONF, "cover_proxy", True)
    monkeypatch.setitem(proxy_app.CONF, "cover_cache_dir", str(tmp_path / "covers"))
    monkeypatch.setattr(proxy_app, "download_cover_bytes", _fail)
    proxy_app._COVER_PROXY_FAILED.clear()

    url = "http://cdn.example.com/broken-cover.jpg"
    assert await proxy_app._cover_proxy_response(url) is None
    assert await proxy_app._cover_proxy_response(url) is None
    assert calls["n"] == 1


def test_cover_probe_logs_outcome(monkeypatch, caplog):
    """封面探针：日志里能看到每条封面请求落到了什么形态（诊断用）。"""
    monkeypatch.setitem(proxy_app.CONF, "cover_probe", True)
    guid = _stub_daily(monkeypatch, [{"guid": "online:lx:wy:1", "title": "t"}])
    _stub_online_chain(monkeypatch, info=None)
    _upstream()

    with caplog.at_level("INFO"):
        with TestClient(app) as client:
            client.get(f"/music/api/v1/static/cover/{guid}")
    assert any("cover probe:" in r.getMessage() for r in caplog.records)
