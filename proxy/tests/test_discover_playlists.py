"""平台推荐歌单（发现·歌单广场）注入链测试。

覆盖：
  - guid 契约（discover 前缀、source/playlist_id 清洗与反解）
  - 平台歌单 → 在线条目 id 必须是洛雪形态 `lx:<platform>:<id>`（播放链前提）
  - playlist_list / playlist_detail / batch-detail / playlist_track_list 四个端点注入
  - 静态封面端点对 discover 歌单 302 到平台封面直链
  - 开关关闭时零注入（不影响官方透传）
"""
import os

import httpx
import pytest
from fastapi.testclient import TestClient

import proxy.app as appmod
from proxy import platform_playlists as plpl
from proxy import recommend as dailyrec
from proxy.app import (
    CONF,
    _DISCOVER_CACHE,
    _DISCOVER_CARD_INDEX,
    _DISCOVER_COVER_FORWARD,
    _DISCOVER_COVER_REVERSE,
    _DISCOVER_TRACK_CACHE,
    _SEARCH_CACHE,
    app,
)


@pytest.fixture(autouse=True)
def setup_discover_env(tmp_path, monkeypatch):
    _SEARCH_CACHE.clear()
    _DISCOVER_CACHE.clear()
    _DISCOVER_TRACK_CACHE.clear()
    _DISCOVER_COVER_REVERSE.clear()
    _DISCOVER_COVER_FORWARD.clear()
    _DISCOVER_CARD_INDEX.clear()
    # 卡片索引的「已从磁盘加载」标记也要复位，否则上一用例的 tmp 路径会被沿用
    monkeypatch.setattr(appmod, "_DISCOVER_CARD_INDEX_LOADED", False)
    monkeypatch.setenv("FNMUSIC_RECOMMEND_DIR", str(tmp_path / "recommend_cache"))
    monkeypatch.setenv("FNMUSIC_PLAY_HISTORY_DIR", str(tmp_path / "play_history"))
    monkeypatch.setitem(CONF, "fav_dir", str(tmp_path / "online_favorites"))
    monkeypatch.setitem(CONF, "cache_dir", str(tmp_path / "cache"))
    monkeypatch.setitem(CONF, "recommend_daily", False)
    monkeypatch.setitem(CONF, "recommend_hot", False)
    monkeypatch.setitem(CONF, "recommend_playlist", True)
    monkeypatch.setitem(CONF, "recommend_playlist_source", "")
    monkeypatch.setitem(CONF, "recommend_playlist_page_size", 12)
    # lxmusic 客户端默认 mock（404），防测试触达真实 127.0.0.1:8772
    app.state.lx_client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(404, json={"ok": False})),
        base_url="http://127.0.0.1:8772",
    )
    yield
    _DISCOVER_CACHE.clear()
    _DISCOVER_TRACK_CACHE.clear()


def _auth_user(guid="user-disc-1"):
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/user/me"):
            return httpx.Response(200, json={"code": 0, "data": {"guid": guid}})
        if path.endswith("/playlist/list"):
            return httpx.Response(
                200,
                json={"code": 0, "data": {"list": [{"guid": "localpl", "name": "本地歌单",
                                                   "coverId": "c1", "createdAt": 1, "updatedAt": 1}],
                                          "total": 1}},
            )
        return httpx.Response(200, json={"code": 0, "data": {"list": [], "total": 0}})
    return handler


# ------------------------------------------------------------- guid 契约 --

def test_discover_guid_roundtrip():
    guid = dailyrec.discover_playlist_guid("wy", "12345")
    assert guid == "online:playlist:discover:wy:12345"
    assert dailyrec.online_playlist_kind(guid) == "discover"
    assert dailyrec.is_discover_playlist_guid(guid)
    assert dailyrec.is_recommend_playlist_guid(guid)
    assert dailyrec.parse_discover_playlist_guid(guid) == ("wy", "12345")


def test_discover_guid_sanitizes_playlist_id():
    # 酷我 digit-8__123 形态可原样保留；非法字符转下划线
    guid = dailyrec.discover_playlist_guid("kw", "digest-8__3677150229")
    assert guid == "online:playlist:discover:kw:digest-8__3677150229"
    assert dailyrec.parse_discover_playlist_guid(guid) == ("kw", "digest-8__3677150229")

    weird = dailyrec.discover_playlist_guid("tx", "a/b?c=d")
    assert "/" not in weird.split("discover:")[1]
    assert dailyrec.parse_discover_playlist_guid(weird) == ("tx", "a_b_c_d")


def test_parse_discover_guid_rejects_bad_forms():
    assert dailyrec.parse_discover_playlist_guid("") == ("", "")
    assert dailyrec.parse_discover_playlist_guid("online:playlist:daily:20260101") == ("", "")
    assert dailyrec.parse_discover_playlist_guid("online:playlist:discover:wy") == ("", "")
    assert dailyrec.parse_discover_playlist_guid("online:playlist:discover::123") == ("", "")
    assert not dailyrec.is_discover_playlist_guid("online:playlist:hot:20260101")


# -------------------------------------------------- 平台歌单 → 在线条目 --

def test_song_id_uses_lx_form():
    """平台曲目的在线 id 必须是 lx:<platform>:<id>，否则 _source_enabled 会拒播。"""
    item = plpl._song("wy", "1415111009", "标题", "歌手")
    assert item["id"] == "lx:wy:1415111009"
    assert item["source"] == "lx"
    assert item["lx_source"] == "wy"
    # 空 identifier 直接丢弃
    assert plpl._song("wy", "", "标题") == {}


def test_supported_platforms_match_lx_contract():
    # 与 lxmusic-service SUPPORTED_PLATFORMS 必须一致，否则下发的 guid 播不了
    assert set(plpl.SUPPORTED_PLATFORMS) == {"kw", "kg", "tx", "wy", "mg"}


def test_map_kg_song_splits_filename_and_picks_cover():
    raw = {
        "hash": "35752B1309268B40355A1CEE7821EDEE",
        "filename": "郭芊彤 - 时光你等等我",
        "remark": "", "timelength": 259000,
        "trans_param": {"union_cover": "http://imge.kugou.com/stdmusic/{size}/x.jpg"},
    }
    song = plpl._map_kg_song(raw)
    assert song["id"] == "lx:kg:35752B1309268B40355A1CEE7821EDEE"
    assert song["artist"] == "郭芊彤"
    assert song["title"] == "" or song["title"] == "时光你等等我"
    assert "{size}" not in song["cover_url"] and song["cover_url"].endswith("/400/x.jpg")


def test_map_mg_song_prefixes_relative_cover():
    raw = {"objectInfo": {
        "copyrightId": "6005861N9XR", "songId": "1", "songName": "程艾影",
        "album": "署前街少年", "duration": 300, "albumImg": None,
        "img1": "/data/oss/resource/00/5w/rn/x.webp",
        "singerList": [{"name": "赵雷"}],
    }}
    song = plpl._map_mg_song(raw)
    assert song["id"] == "lx:mg:6005861N9XR"
    assert song["artist"] == "赵雷"
    assert song["cover_url"].startswith("https://d.musicapp.migu.cn/")


def test_extract_mg_items_prefers_resid_over_4006_banner():
    contents = [
        {"viewId": "4006-78509400", "txt": "运营位标题", "img": "http://x/1.webp"},
        {"viewId": "4025-233231344", "resId": "233231344", "resType": "2021",
         "txt": "对你的思念都唱进了民谣里", "img": "http://x/2.webp"},
    ]
    out: list = []
    plpl._extract_mg_items(contents, out, set())
    ids = [c["id"] for c in out]
    assert ids == ["233231344"]  # 4006 运营位被排除


# ------------------------------------------------------ 四个端点注入 --

def _patch_platform(monkeypatch, source, cards, tracks):
    """把某平台的推荐列表/曲目拉取替换为内存桩。"""
    async def fake_cards(client, src, sort="hot", page=1, limit=30):
        return cards if src == source else []

    async def fake_tracks(client, src, pid, limit=100):
        return tracks if src == source and pid == cards[0]["id"] else []

    monkeypatch.setitem(plpl._RECOMMEND_MAP, source, None)
    monkeypatch.setattr(plpl, "fetch_platform_playlists", fake_cards)
    monkeypatch.setattr(plpl, "fetch_playlist_tracks", fake_tracks)


def _stub_cards():
    return [{
        "id": "PLID1", "name": "深夜民谣", "author": "张三", "img": "http://img.test/1.jpg",
        "play_count": "1.2万", "total": 30, "desc": "介绍", "source": "wy",
    }]


def _stub_tracks():
    return [
        plpl._song("wy", "111", "曲目一", "歌手甲", "专辑甲", 200.0, "mp3", "http://c/1.jpg"),
        plpl._song("wy", "222", "曲目二", "歌手乙", "专辑乙", 180.0, "mp3", "http://c/2.jpg"),
    ]


def test_playlist_list_injects_discover_cards(monkeypatch):
    _patch_platform(monkeypatch, "wy", _stub_cards(), _stub_tracks())
    app.state.upstream_client = httpx.AsyncClient(
        transport=httpx.MockTransport(_auth_user()), base_url="http://unix"
    )
    with TestClient(app) as client:
        resp = client.get("/music/api/v1/playlist/list")
        assert resp.status_code == 200
        data = resp.json()["data"]
        discs = [it for it in data["list"]
                 if dailyrec.is_discover_playlist_guid(str(it.get("guid") or ""))]
        assert len(discs) == 1
        card = discs[0]
        assert card["guid"] == "online:playlist:discover:wy:PLID1"
        assert card["name"] == "深夜民谣"
        # coverId 伪装成官方 track_+32hex，且能反解回平台封面直链
        assert card["coverId"].startswith("track_") and len(card["coverId"]) == 6 + 32
        assert _DISCOVER_COVER_REVERSE.get(card["coverId"][6:]) == "http://img.test/1.jpg"
        assert data["total"] == len(data["list"])


def test_playlist_detail_returns_discover_card(monkeypatch):
    _patch_platform(monkeypatch, "wy", _stub_cards(), _stub_tracks())
    app.state.upstream_client = httpx.AsyncClient(
        transport=httpx.MockTransport(_auth_user()), base_url="http://unix"
    )
    with TestClient(app) as client:
        resp = client.get("/music/api/v1/playlist/detail",
                          params={"guid": "online:playlist:discover:wy:PLID1"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["code"] == 0
        assert body["data"]["guid"] == "online:playlist:discover:wy:PLID1"
        assert body["data"]["trackCount"] == 2


def test_batch_detail_returns_discover_card(monkeypatch):
    _patch_platform(monkeypatch, "wy", _stub_cards(), _stub_tracks())
    app.state.upstream_client = httpx.AsyncClient(
        transport=httpx.MockTransport(_auth_user()), base_url="http://unix"
    )
    with TestClient(app) as client:
        resp = client.get("/music/api/v1/playlist/batch-detail",
                          params={"guids": "online:playlist:discover:wy:PLID1"})
        assert resp.status_code == 200
        lst = resp.json()["data"]["list"]
        assert len(lst) == 1 and lst[0]["guid"] == "online:playlist:discover:wy:PLID1"


def test_playlist_track_list_returns_track_vos(monkeypatch):
    _patch_platform(monkeypatch, "wy", _stub_cards(), _stub_tracks())
    app.state.upstream_client = httpx.AsyncClient(
        transport=httpx.MockTransport(_auth_user()), base_url="http://unix"
    )
    with TestClient(app) as client:
        resp = client.get("/music/api/v1/track/playlist-detail/list",
                          params={"playlistGUID": "online:playlist:discover:wy:PLID1"})
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["total"] == 2
        assert len(data["list"]) == 2
        # 曲目 guid 伪装成官方 32hex，但能反解回 online:lx:wy:<id>
        from proxy.app import resolve_real_guid
        first = data["list"][0]
        assert resolve_real_guid(str(first["guid"])) == "online:lx:wy:111"
        # VO 必备字段：artists / album / audioSpec / duration（毫秒）
        assert first["artists"] and first["album"]["name"] == "专辑甲"
        assert first["audioSpec"]["format"] == "mp3"
        assert first["duration"] == 200000


def test_static_cover_redirects_to_platform_cover(monkeypatch):
    _patch_platform(monkeypatch, "wy", _stub_cards(), _stub_tracks())
    app.state.upstream_client = httpx.AsyncClient(
        transport=httpx.MockTransport(_auth_user()), base_url="http://unix"
    )
    with TestClient(app) as client:
        # 先触发 list 注入登记封面反查
        client.get("/music/api/v1/playlist/list")
        guid = "online:playlist:discover:wy:PLID1"
        resp = client.get("/music/api/v1/static/cover", params={"guid": guid},
                          follow_redirects=False)
        assert resp.status_code == 302
        assert resp.headers["location"] == "http://img.test/1.jpg"


def test_discover_disabled_no_injection(monkeypatch):
    _patch_platform(monkeypatch, "wy", _stub_cards(), _stub_tracks())
    monkeypatch.setitem(CONF, "recommend_playlist", False)
    app.state.upstream_client = httpx.AsyncClient(
        transport=httpx.MockTransport(_auth_user()), base_url="http://unix"
    )
    with TestClient(app) as client:
        resp = client.get("/music/api/v1/playlist/list")
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert not [it for it in data["list"]
                    if dailyrec.is_discover_playlist_guid(str(it.get("guid") or ""))]
        # 官方歌单照常下发
        assert any(it.get("guid") == "localpl" for it in data["list"])


def test_discover_source_whitelist(monkeypatch):
    _patch_platform(monkeypatch, "wy", _stub_cards(), _stub_tracks())
    monkeypatch.setitem(CONF, "recommend_playlist_source", "tx")  # 只开 QQ
    app.state.upstream_client = httpx.AsyncClient(
        transport=httpx.MockTransport(_auth_user()), base_url="http://unix"
    )
    with TestClient(app) as client:
        data = client.get("/music/api/v1/playlist/list").json()["data"]
        assert not [it for it in data["list"]
                    if dailyrec.is_discover_playlist_guid(str(it.get("guid") or ""))]


def test_discover_sources_defaults_to_all_supported(monkeypatch):
    from proxy.app import _discover_sources
    monkeypatch.setitem(CONF, "recommend_playlist_source", "")
    assert _discover_sources() == list(plpl.SUPPORTED_PLATFORMS)
    monkeypatch.setitem(CONF, "recommend_playlist_source", "bogus,wy,")
    assert _discover_sources() == ["wy"]


# ------------------------------------------------- 歌单名不再退化为通用名 --
#
# 实测问题：一开始歌单列表显示真实歌单名（如「深夜民谣」），后来只显示
# 「QQ音乐推荐歌单」「网易云音乐推荐歌单」。原因：
#   - playlist/list 的卡片真名来自上游抓取，而抓取缓存只在进程内存里（30 分钟 TTL，
#     重启即空），上游弱网/限流时整块为空 → playlist/list 不再下发卡片；
#   - App 仍持有先前学到的 guid，转而用 playlist/detail、playlist/batch-detail
#     反查，而 _discover_detail_record 把名称**硬编码**成平台通用名。
# 修复：卡片元数据落盘（可跨重启/上游故障），且详情端点优先使用真名。

DISCOVER_GUID = "online:playlist:discover:wy:PLID1"


def _open_list(client):
    """打一次 playlist/list，触发卡片抓取与落盘。"""
    return client.get("/music/api/v1/playlist/list").json()["data"]


def test_detail_endpoints_use_real_playlist_name(monkeypatch):
    """真名已知时，detail / batch-detail 也必须回真名（不能硬编码通用名）。"""
    _patch_platform(monkeypatch, "wy", _stub_cards(), _stub_tracks())
    app.state.upstream_client = httpx.AsyncClient(
        transport=httpx.MockTransport(_auth_user()), base_url="http://unix"
    )
    with TestClient(app) as client:
        _open_list(client)
        detail = client.get("/music/api/v1/playlist/detail",
                            params={"guid": DISCOVER_GUID}).json()["data"]
        assert detail["name"] == "深夜民谣"
        batch = client.get("/music/api/v1/playlist/batch-detail",
                           params={"guids": DISCOVER_GUID}).json()["data"]["list"]
        assert batch[0]["name"] == "深夜民谣"


def test_card_name_survives_restart(monkeypatch):
    """重启（内存全清）后仍能给出真名 —— 靠落盘索引，而非内存缓存。"""
    _patch_platform(monkeypatch, "wy", _stub_cards(), _stub_tracks())
    app.state.upstream_client = httpx.AsyncClient(
        transport=httpx.MockTransport(_auth_user()), base_url="http://unix"
    )
    with TestClient(app) as client:
        _open_list(client)
    # 模拟进程重启：清空所有进程内状态
    _DISCOVER_CACHE.clear()
    _DISCOVER_TRACK_CACHE.clear()
    _DISCOVER_COVER_REVERSE.clear()
    _DISCOVER_COVER_FORWARD.clear()
    _DISCOVER_CARD_INDEX.clear()
    monkeypatch.setattr(appmod, "_DISCOVER_CARD_INDEX_LOADED", False)

    with TestClient(app) as client:
        detail = client.get("/music/api/v1/playlist/detail",
                            params={"guid": DISCOVER_GUID}).json()["data"]
        assert detail["name"] == "深夜民谣"


def test_upstream_failure_falls_back_to_persisted_cards(monkeypatch):
    """上游抓取失败时，歌单列表仍用落盘索引里的卡片（含真名），不整块消失。"""
    _patch_platform(monkeypatch, "wy", _stub_cards(), _stub_tracks())
    app.state.upstream_client = httpx.AsyncClient(
        transport=httpx.MockTransport(_auth_user()), base_url="http://unix"
    )
    with TestClient(app) as client:
        _open_list(client)
    _DISCOVER_CACHE.clear()
    _DISCOVER_CARD_INDEX.clear()
    monkeypatch.setattr(appmod, "_DISCOVER_CARD_INDEX_LOADED", False)

    async def no_cards(client, src, sort="hot", page=1, limit=30):
        return []

    monkeypatch.setattr(plpl, "fetch_platform_playlists", no_cards)
    with TestClient(app) as client:
        data = client.get("/music/api/v1/playlist/list").json()["data"]
    discs = [it for it in data["list"]
             if dailyrec.is_discover_playlist_guid(str(it.get("guid") or ""))]
    assert len(discs) == 1
    assert discs[0]["name"] == "深夜民谣"


def test_generic_name_only_when_card_never_fetched(monkeypatch):
    """确实从未抓到过卡片时，才退回平台通用名（保证条目可读，不是空字符串）。"""
    from proxy.app import _discover_detail_record
    rec = _discover_detail_record(DISCOVER_GUID, "wy", "PLID1", 0)
    assert rec["name"] == "网易云推荐歌单"
