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
        # 详情页封面：detail 下发的 coverId 也必须能取到图（否则点进去无图）。
        # 本用例未先走 playlist/list，卡片封面未登记 → 走「拉曲目取首图」兜底，
        # 取到曲目封面即可（重点是**不能**退成占位图）。
        cover_id = body["data"]["coverId"]
        assert cover_id.startswith("track_")
        cover = client.get("/music/api/v1/static/cover", params={"coverId": cover_id},
                           follow_redirects=False)
        assert cover.status_code == 302, f"详情页 coverId 未出图: {cover.content[:120]}"
        assert cover.headers["location"] in ("http://img.test/1.jpg", "http://c/1.jpg")


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


def test_discover_tracks_persisted_to_disk(monkeypatch):
    """广场歌单曲目必须落盘（否则重启后 warm 重建不出 fake 映射）。"""
    import json
    import os
    _patch_platform(monkeypatch, "wy", _stub_cards(), _stub_tracks())
    app.state.upstream_client = httpx.AsyncClient(
        transport=httpx.MockTransport(_auth_user()), base_url="http://unix"
    )
    with TestClient(app) as client:
        client.get("/music/api/v1/track/playlist-detail/list",
                   params={"playlistGUID": "online:playlist:discover:wy:PLID1"})
        path = appmod._discover_track_path("wy", "PLID1")
        assert os.path.exists(path), "曲目未落盘"
        raw = json.load(open(path, encoding="utf-8"))
        assert raw["source"] == "wy" and raw["playlistId"] == "PLID1"
        assert len(raw["tracks"]) == 2
        assert str(raw["tracks"][0]["guid"]).startswith("online:lx:wy:")


def test_discover_track_cover_survives_restart(monkeypatch):
    """重启后列表曲目封面必须仍能出图。

    历史 bug：广场曲目只存进程内 _DISCOVER_TRACK_CACHE，不落盘 →
    ensure_registry_warm() 无从重建 fake↔real 映射 → App 持旧 coverId 取封面
    时 resolve_real_guid 反解失败 → 走到非 online 分支返回列表 JSON → 无图。
    表现为「歌单里前十几首没封面，点进详情又有」（详情会重新登记）。
    """
    _patch_platform(monkeypatch, "wy", _stub_cards(), _stub_tracks())
    app.state.upstream_client = httpx.AsyncClient(
        transport=httpx.MockTransport(_auth_user()), base_url="http://unix"
    )
    with TestClient(app) as client:
        data = client.get("/music/api/v1/track/playlist-detail/list",
                          params={"playlistGUID": "online:playlist:discover:wy:PLID1"}).json()["data"]
        cover_ids = [t["coverId"] for t in data["list"]]
        assert cover_ids and all(c.startswith("track_") for c in cover_ids)

        # === 严格模拟重启：所有进程内表清空 + warm 标记复位 ===
        _DISCOVER_COVER_REVERSE.clear()
        _DISCOVER_COVER_FORWARD.clear()
        _DISCOVER_CARD_INDEX.clear()
        _DISCOVER_TRACK_CACHE.clear()
        _DISCOVER_CACHE.clear()
        appmod._FAKE_GUID_REVERSE.clear()
        appmod._FAKE_ALBUM_REGISTRY.clear()
        monkeypatch.setattr(appmod, "_DISCOVER_CARD_INDEX_LOADED", False)
        monkeypatch.setattr(appmod, "_REGISTRY_WARMED", False)

        # 每首曲目各自对应自己的封面直链（桩数据：曲目一 → http://c/1.jpg，曲目二 → http://c/2.jpg）
        expected = ["http://c/1.jpg", "http://c/2.jpg"]
        assert len(cover_ids) == len(expected)
        for cid, want in zip(cover_ids, expected):
            r = client.get("/music/api/v1/static/cover", params={"coverId": cid},
                           follow_redirects=False)
            ctype = r.headers.get("content-type") or ""
            assert r.status_code == 302, f"重启后未出图: {r.status_code} {r.content[:100]}"
            assert ctype != "application/json", "重启后返回了 JSON（反解失败）"
            assert r.headers["location"] == want


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


def test_static_cover_by_coverid_redirects(monkeypatch):
    """App 实际用的是 coverId，不是 guid —— 这条路必须有封面。

    历史 bug：discover 卡的 coverId fake 由**封面直链**派生，于是
    `resolve_real_guid(coverId)` 反解出的是 `http://...` 直链，拿直链去做
    `is_discover_playlist_guid` 必然 False → 整块 discover 分支被跳过 →
    占位图（列表卡片与详情都没图）。guid 路径能出图，所以老测试全绿。
    """
    _patch_platform(monkeypatch, "wy", _stub_cards(), _stub_tracks())
    app.state.upstream_client = httpx.AsyncClient(
        transport=httpx.MockTransport(_auth_user()), base_url="http://unix"
    )
    with TestClient(app) as client:
        data = client.get("/music/api/v1/playlist/list").json()["data"]
        card = [it for it in data["list"]
                if dailyrec.is_discover_playlist_guid(str(it.get("guid") or ""))][0]
        cover_id = card["coverId"]
        assert cover_id.startswith("track_")
        # 裸 coverId（App 形态）
        resp = client.get("/music/api/v1/static/cover", params={"coverId": cover_id},
                          follow_redirects=False)
        assert resp.status_code == 302, f"coverId 路径未走到 discover 分支: {resp.content[:120]}"
        assert resp.headers["location"] == "http://img.test/1.jpg"
        # 带 size 参数（App 列表缩略图会带）
        resp2 = client.get("/music/api/v1/static/cover",
                           params={"coverId": cover_id, "size": "120"},
                           follow_redirects=False)
        assert resp2.status_code == 302
        assert resp2.headers["location"] == "http://img.test/1.jpg"
        # 路径形态 /static/cover/<coverId>
        resp3 = client.get(f"/music/api/v1/static/cover/{cover_id}",
                           follow_redirects=False)
        assert resp3.status_code == 302
        assert resp3.headers["location"] == "http://img.test/1.jpg"


def test_static_cover_by_coverid_survives_restart(monkeypatch):
    """重启后进程内反查表清空，coverId 仍要靠落盘卡片索引出图。

    App 会长期持有重启前学到的 coverId，重启后直接拿它取封面；此时
    `_DISCOVER_COVER_REVERSE`/`_FAKE_GUID_REVERSE` 都是空的，只有
    `_DISCOVER_CARD_INDEX`（落盘）还认得这张卡。
    """
    _patch_platform(monkeypatch, "wy", _stub_cards(), _stub_tracks())
    app.state.upstream_client = httpx.AsyncClient(
        transport=httpx.MockTransport(_auth_user()), base_url="http://unix"
    )
    with TestClient(app) as client:
        data = client.get("/music/api/v1/playlist/list").json()["data"]
        card = [it for it in data["list"]
                if dailyrec.is_discover_playlist_guid(str(it.get("guid") or ""))][0]
        cover_id = card["coverId"]
        guid = card["guid"]
        # 模拟重启：清空全部进程内表，但磁盘索引文件保留
        _DISCOVER_COVER_REVERSE.clear()
        _DISCOVER_COVER_FORWARD.clear()
        appmod._FAKE_GUID_REVERSE.clear()
        _DISCOVER_CARD_INDEX.clear()
        monkeypatch.setattr(appmod, "_DISCOVER_CARD_INDEX_LOADED", False)
        # coverId 仍应出图（经落盘索引反查歌单 guid → 直链）
        resp = client.get("/music/api/v1/static/cover", params={"coverId": cover_id},
                          follow_redirects=False)
        assert resp.status_code == 302, f"重启后 coverId 未出图: {resp.content[:120]}"
        assert resp.headers["location"] == "http://img.test/1.jpg"
        assert appmod._discover_guid_by_cover(cover_id) == guid


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


# --------------------------------------------------------- 分类筛选（2.8.0） --

def test_discover_categories_for_parses_csv(monkeypatch):
    """FNMUSIC_RECOMMEND_PLAYLIST_CATEGORY 解析：按平台过滤、去重、忽略脏值。"""
    monkeypatch.setitem(CONF, "recommend_playlist_category",
                        "tx:11, tx:194 ,kg:12,wy:古风,xinhao,bad:,tx:11")
    assert appmod._discover_categories_for("tx") == ["11", "194"]
    assert appmod._discover_categories_for("kg") == ["12"]
    assert appmod._discover_categories_for("wy") == ["古风"]
    assert appmod._discover_categories_for("kw") == []
    # 空配置 = 无分类（走「全部」，与原行为一致）
    monkeypatch.setitem(CONF, "recommend_playlist_category", "")
    assert appmod._discover_categories_for("tx") == []


def test_fetch_cards_per_category_merges_and_dedupes(monkeypatch):
    """勾选多个分类时：逐分类抓取 → 合并 → 按 pid 去重。

    上游可能把同一张歌单挂在多个标签下，这里必须去重否则列表出现重复卡片。
    """
    seen_cats = []

    async def fake_cards(client, src, sort="hot", page=1, limit=30, category=""):
        seen_cats.append(category)
        if category == "11":
            return [{"id": "A", "name": "摇滚A", "author": "", "img": "", "play_count": "",
                     "total": 1, "desc": "", "source": "tx"}]
        if category == "194":
            # 故意含一张与 "11" 重复的 A，验证去重
            return [{"id": "A", "name": "摇滚A", "author": "", "img": "", "play_count": "",
                     "total": 1, "desc": "", "source": "tx"},
                    {"id": "B", "name": "古风B", "author": "", "img": "", "play_count": "",
                     "total": 1, "desc": "", "source": "tx"}]
        return []

    monkeypatch.setattr(plpl, "fetch_platform_playlists", fake_cards)
    monkeypatch.setitem(CONF, "recommend_playlist_category", "tx:11,tx:194")

    import asyncio
    cards = asyncio.run(appmod._fetch_discover_cards("tx"))
    assert seen_cats == ["11", "194"]
    assert [c["id"] for c in cards] == ["A", "B"]


def test_fetch_cards_no_category_uses_plain_fetch(monkeypatch):
    """未配置分类时必须走原路径（category 传空串），保证零行为变化。"""
    calls = []

    async def fake_cards(client, src, sort="hot", page=1, limit=30, category=""):
        calls.append(category)
        return [{"id": "X", "name": "X", "author": "", "img": "", "play_count": "",
                 "total": 1, "desc": "", "source": "tx"}]

    monkeypatch.setattr(plpl, "fetch_platform_playlists", fake_cards)
    monkeypatch.setitem(CONF, "recommend_playlist_category", "")
    import asyncio
    cards = asyncio.run(appmod._fetch_discover_cards("tx"))
    assert calls == [""]
    assert [c["id"] for c in cards] == ["X"]


def test_tx_recommend_uses_tag_method_with_category_id(monkeypatch):
    """回归（v2.8.3）：tx 必须走 get_playlist_by_tag 且把分类 id 放进 param.id。

    背景：早前误用 get_playlist_by_category —— 该 method 的 id/titleid **不参与分类
    过滤**，恒返回一个与分类无关的固定小集合（实测 total=9，多为「音乐节」歌单），
    导致用户「选国语却出现日本/新奥尔良音乐节」。
    正确做法：get_playlist_by_tag + id=<分类id>（实测 total 随分类变化：
    国语 4812、流行 5325、民谣 830），空分类则 id=10000000（全部）。
    """
    captured = {}

    async def fake_get(client, url, headers, **kw):
        captured["url"] = url
        return {"playlist": {"data": {"v_playlist": []}}}

    monkeypatch.setattr(plpl, "_get", fake_get)
    import asyncio
    from urllib.parse import unquote

    # 指定分类：必须是 by_tag + id=165
    asyncio.run(plpl._tx_recommend(None, "hot", 1, 10, "165"))
    url = unquote(captured["url"])
    assert "get_playlist_by_tag" in url, f"应走 by_tag，实际 {url}"
    assert "get_playlist_by_category" not in url, f"不得再回退到 by_category：{url}"
    assert '"id": 165' in url, f"分类 id 必须进 param.id：{url}"

    # 空分类：仍走 by_tag，id=10000000（全部）
    asyncio.run(plpl._tx_recommend(None, "hot", 1, 10, ""))
    url2 = unquote(captured["url"])
    assert "get_playlist_by_tag" in url2
    assert '"id": 10000000' in url2, f"空分类应为全部：{url2}"

    # 非数字分类：不得丢给 int() 崩，应退化为全部
    asyncio.run(plpl._tx_recommend(None, "hot", 1, 10, "abc"))
    url3 = unquote(captured["url"])
    assert "get_playlist_by_tag" in url3 and '"id": 10000000' in url3, url3


def test_fetch_platform_categories_fallback(monkeypatch):
    """上游分类接口失败时必须回落内置兜底表，绝不返回空（否则 UI 分类区消失）。"""
    async def boom(client, source):
        raise RuntimeError("upstream down")

    monkeypatch.setattr(plpl, "_tx_categories", boom)
    import asyncio
    cats = asyncio.run(plpl.fetch_platform_categories(None, "tx"))
    assert cats and all({"id", "name", "group"} <= set(c) for c in cats)
    # 不支持分类的平台直接返回空
    assert asyncio.run(plpl.fetch_platform_categories(None, "mg")) == []
    assert asyncio.run(plpl.fetch_platform_categories(None, "nope")) == []


def test_category_platforms_cover_expected():
    """分类支持范围：tx/wy/kw/kg 有，mg 没有（咪咕无分类体系）。"""
    assert set(plpl.CATEGORY_PLATFORMS) == {"tx", "wy", "kw", "kg"}
    assert plpl.CATEGORY_GROUP_FALLBACK["mg"] == []
    for src in plpl.CATEGORY_PLATFORMS:
        assert plpl.CATEGORY_GROUP_FALLBACK[src], f"{src} 兜底表不应为空"
