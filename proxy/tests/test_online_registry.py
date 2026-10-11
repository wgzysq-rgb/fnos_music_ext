"""在线曲目元数据登记表测试。

覆盖两层：
  1. `proxy/online_registry.py` 自身（提取 / 读写 / 回填语义 / 持久化 / TTL）；
  2. **端到端回归**：推荐歌单曲目不走搜索 → lxserver 返回合成的"未知曲目/未知歌手"
     → `track/metadata` 必须回填出歌单接口给的真实标题与歌手。
     （用户实测报的正是这个 bug：能播、专辑名对、曲名与歌手显示"未知"。）
"""
import json
import time

import httpx
import pytest
from fastapi.testclient import TestClient

from proxy import online_registry as onreg
from proxy import platform_playlists as plpl
from proxy.app import (
    CONF,
    _DISCOVER_CACHE,
    _DISCOVER_TRACK_CACHE,
    _SEARCH_CACHE,
    app,
)


@pytest.fixture(autouse=True)
def _clean(tmp_path, monkeypatch):
    reset = lambda: onreg.REGISTRY.reset_for_tests(str(tmp_path / "idx.json"))
    reset()
    _SEARCH_CACHE.clear()
    _DISCOVER_CACHE.clear()
    _DISCOVER_TRACK_CACHE.clear()
    monkeypatch.setenv("FNMUSIC_RECOMMEND_DIR", str(tmp_path / "recommend_cache"))
    monkeypatch.setitem(CONF, "lx_enabled", True)
    monkeypatch.setitem(CONF, "lx_sources", [])
    yield
    reset()


# --------------------------------------------------------------- 元数据提取 --

def _vo(guid, title, artist, album, **kw):
    return {
        "guid": guid, "title": title, "artist": artist,
        "album": {"name": album, "guid": f"{guid}:album"},
        "duration_s": 200.0, "ext": "flac", "cover_url": "http://c/1.jpg", **kw,
    }


def test_meta_from_track_vo_extracts_fields():
    meta = onreg.meta_from_track_vo(_vo("online:lx:wy:111", "曲目一", "歌手甲", "专辑甲"))
    assert meta == {
        "guid": "online:lx:wy:111", "title": "曲目一", "artist": "歌手甲",
        "album": "专辑甲", "duration_s": 200.0, "ext": "flac",
        "cover_url": "http://c/1.jpg",
    }


def test_meta_from_track_vo_handles_plain_album_string():
    vo = _vo("online:lx:tx:9", "T", "A", "")
    vo["album"] = "纯字符串专辑"
    assert onreg.meta_from_track_vo(vo)["album"] == "纯字符串专辑"


def test_meta_from_track_vo_rejects_local_guid():
    """本地曲库条目不登记（daily/hot 会混入本地兜底曲目）。"""
    assert onreg.meta_from_track_vo(_vo("local-abc", "T", "A", "AL")) is None
    assert onreg.meta_from_track_vo(_vo("12345", "T", "A", "AL")) is None


def test_meta_from_track_vo_rejects_all_placeholder():
    assert onreg.meta_from_track_vo(_vo("online:lx:wy:1", "", "", "")) is None
    assert onreg.meta_from_track_vo(_vo("online:lx:wy:1", "未知曲目", "未知歌手", "")) is None


def test_record_and_lookup_roundtrip():
    assert onreg.REGISTRY.record({"guid": "online:lx:wy:1", "title": "T", "artist": "A"})
    got = onreg.REGISTRY.lookup("online:lx:wy:1")
    assert got and got["title"] == "T" and got["artist"] == "A"
    assert onreg.REGISTRY.lookup("online:lx:wy:missing") is None


def test_record_tracks_filters_and_counts():
    n = onreg.REGISTRY.record_tracks([
        _vo("online:lx:wy:1", "一", "甲", "专一"),
        _vo("local-x", "二", "乙", "专二"),          # 本地 → 跳过
        _vo("online:lx:wy:3", "", "", ""),          # 全占位 → 跳过
    ])
    assert n == 1
    assert onreg.REGISTRY.stats()["entries"] == 1


# ------------------------------------------------------------------ 回填语义 --

def test_is_placeholder_public():
    """落盘链路用公开出口判"元数据是否真的可用"，占位常量必须全部命中。"""
    for value in ("", None, "未知曲目", "未知歌手", "unknown", "UNKNOWN"):
        assert onreg.is_placeholder(value) is True
    assert onreg.is_placeholder("夜曲") is False


def test_is_placeholder_name_covers_files_and_legacy_names():
    """"艺术家 - 标题" 逐段判定；兼容历史 unknown(编号) 名；哈希名不误伤。"""
    assert onreg.is_placeholder_name("未知歌手 - 未知曲目")
    assert onreg.is_placeholder_name("未知曲目")
    assert onreg.is_placeholder_name("unknown")
    assert onreg.is_placeholder_name("unknown (2)")
    assert not onreg.is_placeholder_name("周杰伦 - 夜曲")
    assert not onreg.is_placeholder_name("unknowncover - 歌名")   # 只是包含 unknown
    assert not onreg.is_placeholder_name("")
    assert not onreg.is_placeholder_name("3f2a1b9c8d7e6f50")     # guid 哈希名


def test_snapshot_returns_copy_of_live_entries():
    onreg.REGISTRY.record({"guid": "online:lx:wy:1", "title": "T", "artist": "A"})
    snap = onreg.REGISTRY.snapshot()
    assert snap["online:lx:wy:1"]["title"] == "T"
    snap["online:lx:wy:1"]["title"] = "改了"
    assert onreg.REGISTRY.lookup("online:lx:wy:1")["title"] == "T"   # 副本，不动内表


def test_apply_replaces_placeholder_values():
    onreg.REGISTRY.record({"guid": "online:lx:wy:1", "title": "真曲目",
                           "artist": "真歌手", "album": "真专辑",
                           "duration_s": 180.0, "cover_url": "http://c/x.jpg"})
    out = onreg.REGISTRY.apply("online:lx:wy:1", {
        "title": "未知曲目", "artist": "未知歌手", "album": "",
        "duration_s": 0, "cover_url": "", "ext": "mp3", "lyric": "…",
    })
    assert out["title"] == "真曲目"
    assert out["artist"] == "真歌手"
    assert out["album"] == "真专辑"
    assert out["cover_url"] == "http://c/x.jpg"
    # 无关字段原样保留
    assert out["ext"] == "mp3" and out["lyric"] == "…"


def test_apply_never_backfills_duration():
    """**时长必须保持为上游值**（哪怕是 0）。

    回归用例：2026-10-09 版本回填时长后，转码 HLS 用估算时长算分片数，
    声明片数多于 ffmpeg 实际产出 → App 拉到 404 分片 → 整首跳下一首。
    `transcode.py` 的不变量要求声明片数 ≤ 实际片数，只有取实测时长才成立：
    duration_s=0 时 `transcode.ensure_session()` 才会走 `probe_duration()`。
    """
    onreg.REGISTRY.record({"guid": "online:lx:wy:1", "title": "T", "artist": "A",
                           "duration_s": 200.0})
    out = onreg.REGISTRY.apply("online:lx:wy:1",
                               {"title": "未知曲目", "duration_s": 0})
    assert out["duration_s"] == 0, "回填时长会破坏转码 HLS 分片不变量"
    # 上游已有真实时长时同样不动
    out2 = onreg.REGISTRY.apply("online:lx:wy:1", {"duration_s": 187.0})
    assert out2["duration_s"] == 187.0


def test_apply_never_overwrites_real_upstream_values():
    """搜索结果等上游已有真实元数据时必须原样保留（不干扰既有链路）。"""
    onreg.REGISTRY.record({"guid": "online:lx:wy:1", "title": "索引标题", "artist": "索引歌手"})
    out = onreg.REGISTRY.apply("online:lx:wy:1",
                               {"title": "上游标题", "artist": "上游歌手", "album": "上游专辑"})
    assert out["title"] == "上游标题"
    assert out["artist"] == "上游歌手"
    assert out["album"] == "上游专辑"


def test_apply_passes_through_without_registration():
    data = {"title": "未知曲目"}
    assert onreg.REGISTRY.apply("online:lx:wy:none", data) == data


def test_apply_returns_registry_when_upstream_missing():
    onreg.REGISTRY.record({"guid": "online:lx:wy:1", "title": "T", "artist": "A", "album": "AL"})
    out = onreg.REGISTRY.apply("online:lx:wy:1", None)
    assert out["title"] == "T" and out["artist"] == "A"


# ------------------------------------------------------------- 持久化 / TTL --

def test_persist_and_reload(tmp_path, monkeypatch):
    path = str(tmp_path / "sub" / "idx.json")
    monkeypatch.setenv("FNMUSIC_RECOMMEND_DIR", str(tmp_path / "sub"))
    reg = onreg.OnlineTrackRegistry(path=path)
    reg.record({"guid": "online:lx:wy:1", "title": "T", "artist": "A"})
    reg.flush()

    with open(path, encoding="utf-8") as fh:
        assert json.load(fh)["items"]["online:lx:wy:1"]["title"] == "T"

    reloaded = onreg.OnlineTrackRegistry(path=path)
    assert reloaded.lookup("online:lx:wy:1")["artist"] == "A"


def test_expired_entries_are_dropped(tmp_path, monkeypatch):
    path = str(tmp_path / "idx.json")
    monkeypatch.setenv("FNMUSIC_RECOMMEND_DIR", str(tmp_path))
    stale = {
        "version": 1,
        "items": {"online:lx:wy:old": {
            "guid": "online:lx:wy:old", "title": "旧", "artist": "旧",
            "album": "", "duration_s": 0, "ext": "mp3", "cover_url": "",
            "ts": time.time() - onreg._TTL_S - 60,
        }},
    }
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(stale, fh)
    reg = onreg.OnlineTrackRegistry(path=path)
    assert reg.lookup("online:lx:wy:old") is None


def test_corrupt_index_degrades_to_empty(tmp_path, monkeypatch):
    path = str(tmp_path / "idx.json")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("{ not json")
    reg = onreg.OnlineTrackRegistry(path=path)
    assert reg.lookup("anything") is None
    assert reg.record({"guid": "online:lx:wy:1", "title": "T"})
    assert reg.lookup("online:lx:wy:1")["title"] == "T"


# ------------------------------------------------- 端到端：用户报告的 bug --

def _stub_cards():
    return [{"id": "PLID1", "name": "深夜民谣", "author": "张三",
             "img": "http://img.test/1.jpg", "play_count": "1.2万",
             "total": 30, "desc": "介绍", "source": "wy"}]


def _stub_tracks():
    return [
        plpl._song("wy", "111", "曲目一", "歌手甲", "专辑甲", 200.0, "mp3", "http://c/1.jpg"),
        plpl._song("wy", "222", "曲目二", "歌手乙", "专辑乙", 180.0, "mp3", "http://c/2.jpg"),
    ]


def _patch_platform(monkeypatch):
    async def fake_cards(client, src, sort="hot", page=1, limit=30):
        return _stub_cards() if src == "wy" else []

    async def fake_tracks(client, src, pid, limit=100):
        return _stub_tracks() if src == "wy" and pid == "PLID1" else []

    monkeypatch.setattr(plpl, "fetch_platform_playlists", fake_cards)
    monkeypatch.setattr(plpl, "fetch_playlist_tracks", fake_tracks)


def _auth_user():
    def handler(request):
        if request.url.path.endswith("/user/me"):
            return httpx.Response(200, json={"code": 0, "data": {"guid": "u-1"}})
        if request.url.path.endswith("/playlist/list"):
            return httpx.Response(200, json={"code": 0, "data": {"list": [], "total": 0}})
        return httpx.Response(404, json={"code": 404})
    return handler


def _lx_client_returning_placeholder(seen: list):
    """模拟 lxserver 缓存未命中：只返回合成的"未知曲目/未知歌手"。"""
    def handler(request):
        seen.append(str(request.url))
        if request.url.path.endswith("/api/v1/track/info"):
            return httpx.Response(200, json={"ok": True, "data": {
                "id": "lx:wy:111", "source": "wy",
                "title": "未知曲目", "artist": "未知歌手", "album": "",
                "duration_s": 0, "ext": "mp3", "cover_url": "",
            }})
        return httpx.Response(404, json={"ok": False})
    return handler


def test_discover_tracks_register_metadata(monkeypatch):
    """下发歌单曲目列表即登记元数据。"""
    _patch_platform(monkeypatch)
    app.state.upstream_client = httpx.AsyncClient(
        transport=httpx.MockTransport(_auth_user()), base_url="http://unix")
    with TestClient(app) as client:
        resp = client.get("/music/api/v1/track/playlist-detail/list",
                          params={"playlistGUID": "online:playlist:discover:wy:PLID1"})
        assert resp.status_code == 200
        assert resp.json()["data"]["total"] == 2

    meta = onreg.REGISTRY.lookup("online:lx:wy:111")
    assert meta is not None, "歌单曲目下发后应已登记元数据"
    assert meta["title"] == "曲目一"
    assert meta["artist"] == "歌手甲"
    assert meta["album"] == "专辑甲"


def test_metadata_endpoint_backfills_from_registry(monkeypatch):
    """端到端：歌单曲目 → metadata 接口必须给出真实标题/歌手（而非"未知"）。"""
    _patch_platform(monkeypatch)
    lx_seen: list = []
    app.state.upstream_client = httpx.AsyncClient(
        transport=httpx.MockTransport(_auth_user()), base_url="http://unix")
    app.state.lx_client = httpx.AsyncClient(
        transport=httpx.MockTransport(_lx_client_returning_placeholder(lx_seen)),
        base_url="http://127.0.0.1:8772")

    with TestClient(app) as client:
        # 1) 打开歌单（触发曲目下发与登记）
        assert client.get(
            "/music/api/v1/track/playlist-detail/list",
            params={"playlistGUID": "online:playlist:discover:wy:PLID1"},
        ).status_code == 200

        # 2) 点击播放（播放器请求曲目详情）
        resp = client.get("/music/api/v1/track/metadata", params={"guid": "online:lx:wy:111"})

    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["title"] == "曲目一", f'期望真实标题，实际 {data["title"]!r}'
    assert data["artists"][0]["name"] == "歌手甲"
    assert data["album"]["name"] == "专辑甲"
    # 确实走了上游（证明是"回填"而不是"没查上游"）
    assert any("track/info" in u for u in lx_seen)


def test_metadata_without_registration_still_returns_upstream(monkeypatch):
    """未登记的非搜索曲目：行为保持原样（不因新增登记表而回归）。"""
    lx_seen: list = []
    app.state.upstream_client = httpx.AsyncClient(
        transport=httpx.MockTransport(_auth_user()), base_url="http://unix")
    app.state.lx_client = httpx.AsyncClient(
        transport=httpx.MockTransport(_lx_client_returning_placeholder(lx_seen)),
        base_url="http://127.0.0.1:8772")
    with TestClient(app) as client:
        resp = client.get("/music/api/v1/track/metadata", params={"guid": "online:lx:wy:999"})
    assert resp.status_code == 200
    assert resp.json()["data"]["title"] == "未知曲目"


# ---------------------------- 端到端：搜索结果重播（bug 1, v2.8.6） ------------

def _lx_search_returning(items: list):
    """构造只认 /api/v1/search 的洛雪客户端 handler：返回改名后的在线曲目。"""
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path != "/api/v1/search":
            return httpx.Response(404, json={"ok": False})
        return httpx.Response(200, json={"ok": True, "items": items, "errors": {}})
    return handler


def _search_upstream_handler(request: httpx.Request) -> httpx.Response:
    # 本地搜索空，全部走在线；同时兼容官方搜索响应形态
    return httpx.Response(200, json={"code": 0, "data": {"list": [], "total": 0}})


def test_search_results_register_metadata(monkeypatch):
    """回归 bug 1（v2.8.6）：搜索结果里的在线曲目现在也要登记进持久化 REGISTRY。

    此前它们只存在内存 _SEARCH_CACHE，代理重启 / lxserver 缓存逐出后再播放
    （最近播放）时 metadata 只剩占位值"未知曲目"，而音频与歌词仍正常。
    """
    lx_items = [
        {"id": "lx:kw:447197599", "lx_source": "kw", "title": "短视频曲目一",
         "artist": "歌手A", "album": "专辑A", "duration_s": 180.0, "ext": "mp3",
         "cover_url": "http://c/k.jpg", "file_size": 1000},
        {"id": "lx:wy:111", "lx_source": "wy", "title": "在线曲目二",
         "artist": "歌手B", "album": "专辑B", "duration_s": 200.0, "ext": "flac",
         "cover_url": "http://c/w.jpg", "file_size": 1200},
    ]
    app.state.upstream_client = httpx.AsyncClient(
        transport=httpx.MockTransport(_search_upstream_handler), base_url="http://unix")
    app.state.lx_client = httpx.AsyncClient(
        transport=httpx.MockTransport(_lx_search_returning(lx_items)),
        base_url="http://127.0.0.1:8772")

    with TestClient(app) as client:
        resp = client.get("/music/api/v1/search/track?q=test&page=1&size=50")
        assert resp.status_code == 200

    meta1 = onreg.REGISTRY.lookup("online:lx:kw:447197599")
    assert meta1 is not None, "搜索结果里的在线曲目应登记进 REGISTRY"
    assert meta1["title"] == "短视频曲目一"
    assert meta1["artist"] == "歌手A"
    meta2 = onreg.REGISTRY.lookup("online:lx:wy:111")
    assert meta2 is not None and meta2["title"] == "在线曲目二"


def test_search_track_replay_backfills_metadata(monkeypatch):
    """端到端回归 bug 1：搜索在线曲目 → 登记 → 模拟"最近播放"重播时 _SEARCH_CACHE
    已失效（重启 / 逐出）、lxserver 缓存也未命中只回占位值，metadata 接口必须用
    登记值回填真实标题 / 歌手（否则用户看到的正是"未知曲目"）。

    测试刻意清空 _SEARCH_CACHE，使 _retained_track 落空，从而**只**检验登记表
    的回填贡献（而非 _SEARCH_CACHE 命中）。
    """
    lx_items = [
        {"id": "lx:kw:447197599", "lx_source": "kw", "title": "短视频曲目一",
         "artist": "歌手A", "album": "专辑A", "duration_s": 180.0, "ext": "mp3",
         "cover_url": "http://c/k.jpg", "file_size": 1000},
    ]
    app.state.upstream_client = httpx.AsyncClient(
        transport=httpx.MockTransport(_search_upstream_handler), base_url="http://unix")
    app.state.lx_client = httpx.AsyncClient(
        transport=httpx.MockTransport(_lx_search_returning(lx_items)),
        base_url="http://127.0.0.1:8772")

    with TestClient(app) as client:
        assert client.get(
            "/music/api/v1/search/track?q=test&page=1&size=50").status_code == 200

    # 模拟重启 / 缓存逐出：搜索内存表清空，只有 REGISTRY（持久化）还在
    _SEARCH_CACHE.clear()

    # 重播：lxserver 缓存已逐出，只回占位值
    lx_seen: list = []
    app.state.upstream_client = httpx.AsyncClient(
        transport=httpx.MockTransport(_auth_user()), base_url="http://unix")
    app.state.lx_client = httpx.AsyncClient(
        transport=httpx.MockTransport(_lx_client_returning_placeholder(lx_seen)),
        base_url="http://127.0.0.1:8772")

    with TestClient(app) as client:
        resp = client.get("/music/api/v1/track/metadata",
                          params={"guid": "online:lx:kw:447197599"})

    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["title"] == "短视频曲目一", f'期望真实标题，实际 {data["title"]!r}'
    assert data["artists"][0]["name"] == "歌手A"
    assert data["album"]["name"] == "专辑A"
    # 确实走了上游（证明是"回填"而不是"没查上游"）
    assert any("track/info" in u for u in lx_seen)
