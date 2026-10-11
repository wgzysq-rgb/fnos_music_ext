"""lxmusic-service/test_client_and_app.py
全套契约与单元测试：覆盖字段映射、探活签名、API 契约和容错机制。
"""

from __future__ import annotations

import asyncio
import json
import logging
import os

import httpx
import pytest

from conftest import lxapp, mock_client
from lxserver_client import (
    extract_track_identifier,
    map_lxserver_song,
    normalize_source,
    parse_interval_to_seconds,
)


# ------------------------------------------------------------- 映射函数单测 --

def test_normalize_source():
    assert normalize_source("kugou") == "kg"
    assert normalize_source("KG") == "kg"
    assert normalize_source("163") == "wy"
    assert normalize_source("netease") == "wy"
    assert normalize_source("kuwo") == "kw"
    assert normalize_source("migu") == "mg"
    assert normalize_source("qq") == "tx"
    assert normalize_source("unknown") == ""
    assert normalize_source(None) == ""


def test_parse_interval_to_seconds():
    assert parse_interval_to_seconds("05:24") == 324
    assert parse_interval_to_seconds("00:45") == 45
    assert parse_interval_to_seconds("01:02:03") == 3723
    assert parse_interval_to_seconds(180) == 180
    assert parse_interval_to_seconds(180.5) == 180
    assert parse_interval_to_seconds("") == 0
    assert parse_interval_to_seconds(None) == 0


def test_map_lxserver_song_kuwo():
    raw = {
        "name": "海阔天空",
        "singer": "Beyond",
        "source": "kw",
        "songmid": "5886682",
        "albumName": "乐与怒",
        "interval": "05:24",
        "img": "https://img.test/pic.jpg",
        "types": [{"type": "128k"}, {"type": "320k"}, {"type": "flac"}],
    }
    item = map_lxserver_song(raw)
    assert item["id"] == "lx:kw:5886682"
    assert item["lx_source"] == "kw"
    assert item["title"] == "海阔天空"
    assert item["artist"] == "Beyond"
    assert item["album"] == "乐与怒"
    assert item["duration_s"] == 324
    assert item["ext"] == "flac"
    assert item["cover_url"] == "https://img.test/pic.jpg"
    assert "flac" in item["types"]


def test_map_lxserver_song_kugou_hash():
    raw = {
        "name": "泡沫",
        "singer": "邓紫棋",
        "source": "kg",
        "hash": "F52899ABCDEF",
        "albumName": "X.P.X",
        "interval": "04:18",
        "types": [{"type": "320k", "hash": "F52899ABCDEF"}],
    }
    item = map_lxserver_song(raw)
    assert item["id"] == "lx:kg:f52899abcdef"
    assert item["hash"] == "f52899abcdef"
    assert item["ext"] == "mp3"


def test_map_lxserver_song_migu_copyright():
    raw = {
        "name": "稻香",
        "singer": "周杰伦",
        "source": "mg",
        "copyrightId": "60054701983",
        "interval": 223,
    }
    item = map_lxserver_song(raw)
    assert item["id"] == "lx:mg:60054701983"
    assert item["copyrightId"] == "60054701983"
    assert item["duration_s"] == 223


# ------------------------------------------------------------- 探活与魔数单测 --

def test_media_signature():
    assert lxapp._media_signature(b"fLaC\x00\x00") == "flac"
    assert lxapp._media_signature(b"ID3\x04\x00") == "mp3"
    assert lxapp._media_signature(b"OggS\x00\x02") == "ogg"
    assert lxapp._media_signature(b"RIFF\x00\x00\x00\x00WAVE") == "wav"
    assert lxapp._media_signature(b"\x00\x00\x00 ftypM4A ") == "m4a"
    assert lxapp._media_signature(b"\xff\xf1\x50\x80") == "aac"
    assert lxapp._media_signature(b"<html>error</html>") == ""


@pytest.mark.asyncio
async def test_probe_url_flac_success():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            206,
            headers={"Content-Type": "audio/flac", "Content-Range": "bytes 0-4095/30000000"},
            content=b"fLaC" + b"\x00" * 4092,
        )

    client = mock_client(handler)
    try:
        ok, final_url, ct, size = await lxapp.probe_url(client, "https://media.test/track.flac")
        assert ok is True
        assert ct == "audio/flac"
        assert size == 30000000
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_probe_url_html_rejection():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"Content-Type": "text/html"}, content=b"<html>Denied</html>")

    client = mock_client(handler)
    try:
        ok, _, _, _ = await lxapp.probe_url(client, "https://media.test/track.mp3")
        assert ok is False
    finally:
        await client.aclose()


# ------------------------------------------------------------- API 契约测试 --

def test_healthz_endpoint(test_app_client):
    res = test_app_client.get("/healthz")
    assert res.status_code == 200
    data = res.json()
    assert data["ok"] is True
    assert data["service"] == "fnmusic-lxmusic"
    assert "capabilities" in data
    assert "user_source" in data
    assert "charts" in data
    assert data["user_source"]["configured"] is True


def test_search_endpoint(test_app_client):
    res = test_app_client.get("/api/v1/search?q=海阔天空&sources=kw&limit=10")
    assert res.status_code == 200
    data = res.json()
    assert data["ok"] is True
    assert isinstance(data["items"], list)
    assert len(data["items"]) > 0
    item = data["items"][0]
    assert item["id"] == "lx:kw:5886682"
    assert item["title"] == "海阔天空"
    assert item["artist"] == "Beyond"


def test_recommend_endpoint(test_app_client, fake_lx):
    """榜单推荐：必须真的取到曲目，且传给 lxserver 的是 bangid 不是 id。

    历史教训（2026-10-10）：本用例此前只断言 `isinstance(items, list)`，
    空榜单也永远绿 —— 掩盖了「list 端点只认 bangid、传 id 恒回 Missing bangid」
    导致 hot 歌单长期为空的真实故障。
    """
    res = test_app_client.get("/api/v1/recommend?sources=kw&limit=5")
    assert res.status_code == 200
    data = res.json()
    assert data["ok"] is True
    assert isinstance(data["items"], list)
    # 真的取到数据（fake 会返回搜索结果），不再是「空列表也算过」
    assert len(data["items"]) > 0, f"expected non-empty chart items, errors={data.get('errors')}"
    assert data["errors"] == {}
    # 传给 lxserver 的必须是真实 bangid（0 参数位是 source，1 参数位是 bangid）
    assert fake_lx.leaderboard_list_calls, "get_leaderboard_list 未被调用"
    src, bangid, _page = fake_lx.leaderboard_list_calls[0]
    assert src == "kw"
    assert bangid and not bangid.startswith("kw__"), (
        f"必须传 bangid（裸数字），实际传了 {bangid!r}"
    )


def test_charts_boards_endpoint(test_app_client, fake_lx):
    """榜单列表端点：透传 lxserver boards，id 用 bangid（裸数字，list 端点只认它）。"""
    res = test_app_client.get("/api/v1/charts/boards?source=kg")
    assert res.status_code == 200
    data = res.json()
    assert data["ok"] is True
    assert data["boards"] and data["boards"][0]["id"] == "8888"
    assert data["boards"][0]["name"] == "TOP500"


def test_charts_boards_unsupported_source(test_app_client):
    res = test_app_client.get("/api/v1/charts/boards?source=tx")
    assert res.status_code == 200
    assert res.json()["boards"] == []


def test_charts_list_endpoint(test_app_client, fake_lx):
    """榜单曲目端点：按 bangid 取曲目，非空即证明走通了 lxserver list。"""
    res = test_app_client.get("/api/v1/charts/list?source=kw&bangid=93&limit=5")
    assert res.status_code == 200
    data = res.json()
    assert data["ok"] is True
    assert len(data["items"]) > 0
    assert fake_lx.leaderboard_list_calls
    src, bangid, _page = fake_lx.leaderboard_list_calls[0]
    assert src == "kw" and bangid == "93"


def test_charts_list_missing_bangid_returns_empty(test_app_client):
    res = test_app_client.get("/api/v1/charts/list?source=kg")
    assert res.status_code == 200
    assert res.json()["items"] == []


def test_track_info_endpoint(test_app_client):
    # 未缓存条目返回合成数据
    res = test_app_client.get("/api/v1/track/info?id=lx:kw:5886682")
    assert res.status_code == 200
    data = res.json()
    assert data["ok"] is True
    assert data["data"]["id"] == "lx:kw:5886682"
    assert data["data"]["lx_source"] == "kw"


def test_track_lyric_endpoint(test_app_client):
    res = test_app_client.get("/api/v1/track/lyric?id=lx:kw:5886682")
    assert res.status_code == 200
    data = res.json()
    assert data["ok"] is True
    assert "[00:00.00]" in data["data"]["lyric"]


def test_source_management_endpoints(test_app_client, fake_lx):
    # GET /api/v1/source
    res = test_app_client.get("/api/v1/source")
    assert res.status_code == 200
    assert res.json()["ok"] is True

    # POST /api/v1/source/upload —— 返回 lxserver 真实存储路径（id 由 @name 派生）
    res = test_app_client.post(
        "/api/v1/source/upload",
        json={"filename": "my_src.js", "script": "/*!\n * @name MyTest\n */\nconsole.log(1);"},
    )
    assert res.status_code == 200
    assert res.json()["ok"] is True
    data = res.json()["data"]
    assert data["url"] == "file:///data/lxserver/users/source/_open/MyTest.js"
    assert data["meta"]["name"] == "MyTest"

    # 同一脚本重复上传：复用既有源，不报错（多源列表的常规操作）
    res = test_app_client.post(
        "/api/v1/source/upload",
        json={"filename": "my_src.js", "script": "/*!\n * @name MyTest\n */\nconsole.log(1);"},
    )
    assert res.status_code == 200
    assert res.json()["data"]["url"] == data["url"]

    # POST /api/v1/source（激活上传返回的 file:// URL）
    res = test_app_client.post("/api/v1/source", json={"url": data["url"]})
    assert res.status_code == 200
    assert res.json()["ok"] is True
    # 单源语义：目标启用、其余禁用
    states = {s["id"]: s["enabled"] for s in fake_lx.sources}
    assert states.get("MyTest.js") is True
    assert states.get("source1") is False

    # DELETE /api/v1/source (清除)
    res = test_app_client.delete("/api/v1/source")
    assert res.status_code == 200
    assert res.json()["ok"] is True


@pytest.mark.skipif(os.name != "posix",
                    reason="file:// 本地路径补导入依赖 POSIX 绝对路径（Windows 下 urlparse 会把盘符并进 path）")
def test_source_set_activates_unknown_local_file_by_import(test_app_client, fake_lx, tmp_path):
    """旧数据卷路径（如 /data/lxmusic/uploads）不在 lxserver 列表时自动补导入再激活。"""
    script_path = tmp_path / "legacy_src.js"
    script_path.write_text("/*!\n * @name LegacySrc\n */\nconsole.log(1);", encoding="utf-8")
    res = test_app_client.post("/api/v1/source", json={"url": f"file://{script_path}"})
    assert res.status_code == 200
    assert res.json()["ok"] is True
    # 已按派生 id 导入并激活
    states = {s["id"]: s["enabled"] for s in fake_lx.sources}
    assert states.get("LegacySrc.js") is True


def test_source_set_rejects_missing_source(test_app_client):
    res = test_app_client.post("/api/v1/source", json={"url": "file:///nonexistent/nope.js"})
    assert res.status_code == 500
    assert "未找到要激活的源" in res.json()["error"]


def test_lxserver_upload_sends_json_contract():
    """上传必须发 JSON {filename, content}（lxserver 端点 JSON.parse，multipart 会被拒）。"""
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["content_type"] = request.headers.get("content-type", "")
        captured["body"] = json.loads(request.content.decode())
        return httpx.Response(200, json={"success": True, "id": "x.js", "metadata": {"name": "x"}})

    from lxserver_client import LxServerClient

    client = LxServerClient(base_url="http://test", admin_password="pw")
    client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://test")
    res = asyncio.run(client.upload_custom_source("x.js", "console.log(1)"))
    assert captured["content_type"].startswith("application/json")
    assert captured["body"] == {"filename": "x.js", "content": "console.log(1)"}
    assert res["id"] == "x.js"


def test_lxserver_admin_resp_semantics():
    """管理端点失败语义：success=false / HTTP 500 抛 RuntimeError 带服务端文案。"""
    from lxserver_client import LxServerClient

    def fail_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"success": False, "error": '源 "x" 已存在于 [open]'})

    client = LxServerClient(base_url="http://test", admin_password="pw")
    client._client = httpx.AsyncClient(transport=httpx.MockTransport(fail_handler), base_url="http://test")
    with pytest.raises(RuntimeError, match="已存在"):
        asyncio.run(client.upload_custom_source("x.js", "s"))

    def server_error_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"success": False, "error": "boom"})

    client2 = LxServerClient(base_url="http://test", admin_password="pw")
    client2._client = httpx.AsyncClient(transport=httpx.MockTransport(server_error_handler), base_url="http://test")
    with pytest.raises(RuntimeError, match="boom"):
        asyncio.run(client2.toggle_custom_source("x.js", True))


def test_lxserver_toggle_sends_enabled_key():
    """toggle 必须用 enabled 键（lxserver 端点不认识 enable，会退化成布尔翻转）。"""
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content.decode())
        return httpx.Response(200, json={"success": True, "enabled": True})

    from lxserver_client import LxServerClient

    client = LxServerClient(base_url="http://test", admin_password="pw")
    client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://test")
    assert asyncio.run(client.toggle_custom_source("MyTest.js", True)) is True
    assert captured["body"] == {"id": "MyTest.js", "enabled": True}


def test_track_url_resolution(test_app_client):
    # 模拟探测成功的直链返回
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            206,
            headers={"Content-Type": "audio/flac", "Content-Range": "bytes 0-4095/30000000"},
            content=b"fLaC" + b"\x00" * 4092,
        )

    # 替换全局 client 进行探活
    orig_client = lxapp.app.state.client
    lxapp.app.state.client = mock_client(handler)
    try:
        res = test_app_client.get("/api/v1/track/url?id=lx:kw:5886682&quality=lossless")
        assert res.status_code == 200
        data = res.json()
        assert data["ok"] is True
        assert data["data"]["id"] == "lx:kw:5886682"
        assert data["data"]["ext"] == "flac"
        assert data["data"]["actual_tier"] == "lossless"
        assert "headers" in data["data"]
    finally:
        lxapp.app.state.client = orig_client


def test_verify_source_json_format_guard():
    from verify_source import _looks_like_json_source

    assert _looks_like_json_source('{"api": "https://test.com"}') is True
    assert _looks_like_json_source('[{"api": 1}]') is True
    assert _looks_like_json_source("/* @name test */ console.log(1);") is False


def test_source_set_http_url_activates_target(test_app_client, fake_lx):
    """测试通过 HTTP URL 设置源，能够正确导入并激活该源（单源生效）。"""
    res = test_app_client.post("/api/v1/source", json={"url": "https://example.com/remote_source.js"})
    assert res.status_code == 200
    assert res.json()["ok"] is True
    assert "https://example.com/remote_source.js" in fake_lx.import_calls
    # 验证目标源已被激活，旧源已被禁用
    states = {s["id"]: s["enabled"] for s in fake_lx.sources}
    assert states.get("remote_source.js") is True
    assert states.get("source1") is False


def test_activate_nonexistent_source_does_not_disable_existing():
    """激活不存在的目标源时，不应当误将当前运行中的源全部禁用。"""
    from lxserver_client import LxServerClient

    client = LxServerClient(base_url="http://test")
    # 模拟已有激活源
    sources = [
        {"id": "source1", "name": "source1", "enabled": True},
        {"id": "source2", "name": "source2", "enabled": False},
    ]
    toggled = []

    async def fake_list():
        return list(sources)

    async def fake_toggle(sid, enable):
        toggled.append((sid, enable))
        return True

    client.list_custom_sources = fake_list
    client.toggle_custom_source = fake_toggle

    ok = asyncio.run(client.activate_single_source("non_existent_source"))
    assert ok is False
    # 没有任何 toggle 被执行，旧源保持原样
    assert toggled == []


def test_source_capabilities_reflects_failed_status():
    """当激活源状态为 failed 时，playback_available 应为 False 并给出错误原因。"""
    from app import source_capabilities
    import app as lxapp

    orig_lx = lxapp.LXSERVER
    try:
        class FailedClient:
            async def is_alive(self):
                return True

            async def list_custom_sources(self):
                return [{
                    "id": "bad.js",
                    "name": "bad",
                    "enabled": True,
                    "status": "failed",
                    "error": "script crashed",
                    "supportedSources": ["kw"],
                }]

        lxapp.LXSERVER = FailedClient()
        caps = asyncio.run(source_capabilities())
        for plat, info in caps.items():
            assert info["playback_available"] is False
            assert "failed" in info["reason"]
    finally:
        lxapp.LXSERVER = orig_lx

# ------------------------------------------- 历史数据迁移自举（pathlib 回归） --

def test_bootstrap_migration_has_pathlib_imported():
    """_bootstrap_migration 用 pathlib.Path。

    真机日志曾长期只报「历史数据检查与迁移异常: name 'Path' is not defined」，
    整个历史音源迁移/自动激活被一句 except 静默吞掉。
    """
    assert lxapp.Path is not None


async def _forbidden_source_set(body):
    raise AssertionError("不该调用 source_set：%s" % body.url)


class _ReadyClient:
    """只为迁移自举提供 is_alive / 列表能力的替身（不碰网络）。"""

    def __init__(self, sources=None, uploads=None):
        self.sources = list(sources or [])
        self.uploads = uploads
        self.alive_calls = 0
        self.list_calls = 0

    async def is_alive(self):
        self.alive_calls += 1
        return True

    async def list_custom_sources(self):
        self.list_calls += 1
        return self.sources

    async def upload_custom_source(self, filename, content):
        if self.uploads is not None:
            self.uploads.append((filename, content))
        return {"success": True}


def test_bootstrap_migration_runs_clean_on_empty_env(monkeypatch, caplog, tmp_path):
    """空环境（无历史目录/无历史音源）跑完不得报异常，且确实探活过 lxserver。"""
    fake = _ReadyClient()
    monkeypatch.setenv("LX_DATA_DIR", str(tmp_path / "no-such-legacy"))
    monkeypatch.delenv("LX_SOURCE_URL", raising=False)
    monkeypatch.setattr(lxapp, "LXSERVER", fake)

    with caplog.at_level(logging.WARNING):
        asyncio.run(lxapp._bootstrap_migration())

    assert fake.alive_calls >= 1
    assert "历史数据检查与迁移异常" not in caplog.text
    assert "is not defined" not in caplog.text


def test_bootstrap_migration_uploads_legacy_scripts(monkeypatch, tmp_path):
    """历史 uploads/*.js 必须被迁移上传；已在源列表里的同名脚本跳过。"""
    legacy = tmp_path / "legacy"
    (legacy / "uploads").mkdir(parents=True)
    (legacy / "uploads" / "ikun.js").write_text("/*! @name ikun */", encoding="utf-8")
    (legacy / "uploads" / "old.js").write_text("/*! @name old */", encoding="utf-8")

    uploaded: list[tuple[str, str]] = []
    fake = _ReadyClient(sources=[{"id": "old.js", "name": "old", "enabled": False}],
                        uploads=uploaded)
    monkeypatch.setenv("LX_DATA_DIR", str(legacy))
    monkeypatch.delenv("LX_SOURCE_URL", raising=False)
    monkeypatch.setattr(lxapp, "LXSERVER", fake)
    monkeypatch.setattr(lxapp, "source_set", _forbidden_source_set)

    asyncio.run(lxapp._bootstrap_migration())

    assert [name for name, _ in uploaded] == ["ikun.js"]


def test_bootstrap_migration_reactivates_legacy_source(monkeypatch, tmp_path):
    """没有任何源的场景下，历史 state.json 的 source_url 必须被自动激活。"""
    legacy = tmp_path / "legacy"
    legacy.mkdir(parents=True)
    (legacy / "state.json").write_text(
        json.dumps({"source_url": "https://example.test/src.js"}), encoding="utf-8")

    activated: list[str] = []

    async def fake_source_set(body):
        activated.append(body.url)
        return {"success": True}

    fake = _ReadyClient()
    monkeypatch.setenv("LX_DATA_DIR", str(legacy))
    monkeypatch.delenv("LX_SOURCE_URL", raising=False)
    monkeypatch.setattr(lxapp, "LXSERVER", fake)
    monkeypatch.setattr(lxapp, "source_set", fake_source_set)

    asyncio.run(lxapp._bootstrap_migration())

    assert activated == ["https://example.test/src.js"]


def test_bootstrap_migration_keeps_existing_active_source(monkeypatch, tmp_path):
    """已有激活源时不得用历史 state.json 覆盖用户当前选择。"""
    legacy = tmp_path / "legacy"
    legacy.mkdir(parents=True)
    (legacy / "state.json").write_text(
        json.dumps({"source_url": "https://example.test/stale.js"}), encoding="utf-8")

    fake = _ReadyClient(sources=[{"id": "cur.js", "name": "cur", "enabled": True}])
    monkeypatch.setenv("LX_DATA_DIR", str(legacy))
    monkeypatch.delenv("LX_SOURCE_URL", raising=False)
    monkeypatch.setattr(lxapp, "LXSERVER", fake)
    monkeypatch.setattr(lxapp, "source_set", _forbidden_source_set)

    asyncio.run(lxapp._bootstrap_migration())


# ------------------------------------------------- 榜单 bangid 契约（回归） --

def _leaderboard_transport(record: list):
    """桩：只接受 bangid= 参数，传 id= 一律回 "Missing bangid"（复刻 lxserver 真实行为）。"""
    def handler(request: httpx.Request) -> httpx.Response:
        record.append(dict(request.url.params))
        if request.url.path == "/api/music/leaderboard/boards":
            return httpx.Response(200, json={"list": [
                {"id": "kg__8888", "name": "TOP500", "bangid": "8888"},
                {"id": "kg__6666", "name": "飙升榜", "bangid": "6666"},
            ]})
        if request.url.path == "/api/music/leaderboard/list":
            if "bangid" not in request.url.params:
                return httpx.Response(400, text="Missing bangid")
            return httpx.Response(200, json={"total": 1, "list": [{
                "singer": "歌手", "name": "歌曲", "albumName": "专辑",
                "songmid": 653605791, "source": "kg", "interval": "04:56",
                "img": "http://imge.kugou.com/stdmusic/1000/x.jpg",
                "hash": "ABC", "types": [{"type": "128k", "hash": "H1"}],
            }]})
        return httpx.Response(404)
    return httpx.MockTransport(handler)


@pytest.mark.anyio
async def test_leaderboard_list_uses_bangid_not_id():
    """list 端点必须传 bangid —— 传 id（任何形态）都会被 lxserver 拒为 Missing bangid。"""
    from lxserver_client import LxServerClient

    seen: list = []
    async with httpx.AsyncClient(transport=_leaderboard_transport(seen),
                                 base_url="http://127.0.0.1:8005") as http:
        client = LxServerClient(base_url="http://127.0.0.1:8005")
        client._client = http  # 注入桩
        items = await client.get_leaderboard_list("kg", "8888", page=1)

    assert len(items) == 1
    assert items[0]["id"] == "lx:kg:abc"  # kg hash 小写化
    assert items[0]["cover_url"] == "http://imge.kugou.com/stdmusic/1000/x.jpg"
    # 请求里必须是 bangid=8888，不能是 id=8888 / id=kg__8888
    list_calls = [p for p in seen if "bangid" in p or "id" in p]
    assert any(p.get("bangid") == "8888" for p in list_calls), f"应传 bangid，实际 {list_calls}"
    assert not any("id" in p and "bangid" not in p for p in list_calls), (
        f"不得只传 id（会被拒为 Missing bangid）：{list_calls}"
    )


@pytest.mark.anyio
async def test_leaderboard_list_non200_is_logged(caplog):
    """非 200 不再静默：必须留下 warning（此前整条链无痕）。"""
    from lxserver_client import LxServerClient

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, text="Missing bangid")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler),
                                 base_url="http://127.0.0.1:8005") as http:
        client = LxServerClient(base_url="http://127.0.0.1:8005")
        client._client = http
        with caplog.at_level(logging.WARNING, logger="fnmusic.lxserver"):
            items = await client.get_leaderboard_list("kg", "8888")

    assert items == []
    assert any("leaderboard/list" in r.message and "400" in r.message for r in caplog.records), \
        "非 200 必须打 warning 并带状态码"


@pytest.mark.anyio
async def test_resolve_leaderboard_bangid_prefers_name_hint():
    """resolve_leaderboard_bangid 按名字命中真实 bangid，取不到回落首项。"""
    from lxserver_client import LxServerClient

    async with httpx.AsyncClient(transport=_leaderboard_transport([]),
                                 base_url="http://127.0.0.1:8005") as http:
        client = LxServerClient(base_url="http://127.0.0.1:8005")
        client._client = http
        assert await client.resolve_leaderboard_bangid("kg", "飙升") == "6666"
        assert await client.resolve_leaderboard_bangid("kg", "不存在的榜单") == "8888"
