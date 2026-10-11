"""元数据反查索引（P1）与落盘攒块写（P2）的行为测试。

P1：列表页**逐条目**按 guid 反查落盘快照 / 歌单缓存曲目，旧实现每次都把
play_history / online_favorites / playlist_tracks / recommend_cache / nmpl 下的
**所有 JSON 读+解析**一遍 → O(条目数² × 文件数)。改成进程级索引后语义必须完全不变：

- 快照优先级仍是「播放历史 → 在线收藏 → 歌单附加」，且来源内**新的在前**
  （旧实现用 `reversed(items)`）；
- 同 guid 优先带真实标题/歌手的快照，占位值（未知曲目/未知歌手）不能挡住真名；
- 没有真名时回落到最先遇到的带内容快照；
- 歌单缓存曲目取首个带标题/歌手的命中（与 `_track_in_list` 同判据）；
- **不用 TTL**：刚写入的数据必须立刻可见（否则新收藏的歌一查就是 None）。

P2：落盘写从「逐块同步写」改为「攒块 + `asyncio.to_thread` 写」，字节内容与顺序
必须与直接写完全一致，且缓冲写完即清空。
"""
import asyncio
import json
import os
from pathlib import Path

import httpx
import pytest

import proxy.app as p

GUID = "online:tx:12345"


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    """把五个元数据来源全部指到 tmp，并清空索引（避免跨用例串扰）。"""
    monkeypatch.setattr(p, "_HOME", str(tmp_path))
    monkeypatch.setenv("FNMUSIC_PLAY_HISTORY_DIR", str(tmp_path / "play_history"))
    monkeypatch.setenv("FNMUSIC_RECOMMEND_DIR", str(tmp_path / "recommend_cache"))
    monkeypatch.setenv("FNMUSIC_NM_PLAYLISTS_DIR", str(tmp_path / "nm_cache"))
    monkeypatch.setitem(p.CONF, "plt_dir", str(tmp_path / "playlist_tracks"))
    monkeypatch.setitem(p.CONF, "fav_dir", str(tmp_path / "online_favorites"))
    for name in ("play_history", "recommend_cache", "nm_cache", "playlist_tracks", "online_favorites"):
        os.makedirs(tmp_path / name, exist_ok=True)
    p.reset_meta_index()
    yield
    p.reset_meta_index()


def write_json(path: Path, data) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return path


def history_items(*items):
    """播放历史 / 在线收藏的形状：{"items": [{guid, track}]}，条目最新的在末尾。"""
    return {"items": [{"guid": g, "track": t} for g, t in items]}


def snapshot(title: str, artist: str = "歌手") -> dict:
    return {"title": title, "artist": artist, "album": "专辑"}


# ------------------------------------------------------------------ P1：快照索引

def test_lookup_prefers_real_tags_over_placeholder(tmp_path):
    """历史里残留的「未知曲目/未知歌手」旧快照不能挡住收藏里的真名。"""
    write_json(tmp_path / "play_history" / "u1.json",
               history_items((GUID, snapshot("未知曲目", "未知歌手"))))
    write_json(tmp_path / "online_favorites" / "u1.json",
               history_items((GUID, snapshot("Get Up 10", "Cardi B"))))
    assert p._lookup_online_snapshot(GUID)["title"] == "Get Up 10"


def test_lookup_falls_back_to_first_content_when_all_placeholder(tmp_path):
    """全都没有真名时也要返回（至少能贡献封面），不能是 None。"""
    write_json(tmp_path / "online_favorites" / "u1.json",
               history_items((GUID, snapshot("未知曲目", "未知歌手"))))
    snap = p._lookup_online_snapshot(GUID)
    assert snap is not None and snap["title"] == "未知曲目"


def test_lookup_within_one_file_takes_newest_entry(tmp_path):
    """同一用户文件内多条同 guid：取最新的那条（旧实现 reversed 的语义）。

    历史可能先记下占位值，之后才补上真名。
    """
    write_json(tmp_path / "play_history" / "u1.json",
               history_items((GUID, snapshot("旧标题")), (GUID, snapshot("新标题"))))
    assert p._lookup_online_snapshot(GUID)["title"] == "新标题"


def test_lookup_history_beats_favorites_across_stores(tmp_path):
    write_json(tmp_path / "play_history" / "u1.json", history_items((GUID, snapshot("来自历史"))))
    write_json(tmp_path / "online_favorites" / "u1.json", history_items((GUID, snapshot("来自收藏"))))
    assert p._lookup_online_snapshot(GUID)["title"] == "来自历史"


def test_lookup_finds_playlist_attached_entry(tmp_path):
    """只加过歌单、从未播放/收藏的歌也要能反查（官方绑定与 tee 兜底的元数据来源）。"""
    write_json(tmp_path / "playlist_tracks" / "u1.json",
               {"items": {"pl-1": [{"guid": GUID, "track": snapshot("歌单里的歌")}]}})
    assert p._lookup_online_snapshot(GUID)["title"] == "歌单里的歌"


def test_lookup_sees_newly_written_file_immediately(tmp_path):
    """索引无 TTL：写完立刻可查（TTL 会让"刚收藏的歌"反查不到）。"""
    assert p._lookup_online_snapshot(GUID) is None
    write_json(tmp_path / "online_favorites" / "u1.json", history_items((GUID, snapshot("刚收藏"))))
    assert p._lookup_online_snapshot(GUID)["title"] == "刚收藏"


def test_lookup_sees_content_change_of_existing_file(tmp_path):
    """同一路径内容被改写（大小/时间变了）也要重建索引，不能吃旧结果。"""
    path = write_json(tmp_path / "online_favorites" / "u1.json",
                      history_items((GUID, snapshot("旧"))))
    assert p._lookup_online_snapshot(GUID)["title"] == "旧"
    write_json(path, history_items((GUID, snapshot("新标题更长"))))
    assert p._lookup_online_snapshot(GUID)["title"] == "新标题更长"


def test_lookup_rejects_empty_guid():
    assert p._lookup_online_snapshot("") is None
    assert p._lookup_playlist_cache_track("") is None


def test_lookup_ignores_entries_without_track(tmp_path):
    write_json(tmp_path / "play_history" / "u1.json", {"items": [{"guid": GUID}, {"guid": GUID, "track": "not-a-dict"}]})
    assert p._lookup_online_snapshot(GUID) is None


# --------------------------------------------------- P1：歌单缓存曲目索引

def test_playlist_cache_track_requires_tags(tmp_path):
    """无标题/歌手的条目不算命中（与 `_track_in_list` 同判据）。"""
    write_json(tmp_path / "recommend_cache" / "u1.json",
               {"tracks": [{"guid": GUID, "title": "", "artist": ""}]})
    assert p._lookup_playlist_cache_track(GUID) is None


def test_playlist_cache_track_found_in_recommend_cache(tmp_path):
    write_json(tmp_path / "recommend_cache" / "u1.json",
               {"tracks": [{"guid": GUID, "title": "T", "artist": "A"}]})
    assert p._lookup_playlist_cache_track(GUID)["title"] == "T"


def test_playlist_cache_track_prefers_recommend_cache_over_nm(tmp_path):
    write_json(tmp_path / "recommend_cache" / "u1.json",
               {"tracks": [{"guid": GUID, "title": "推荐缓存", "artist": "A"}]})
    write_json(tmp_path / "nm_cache" / "u1.json",
               {"tracks": [{"guid": GUID, "title": "网易歌单", "artist": "A"}]})
    assert p._lookup_playlist_cache_track(GUID)["title"] == "推荐缓存"


def test_playlist_cache_track_falls_through_untagged_file(tmp_path):
    """前面的文件只有无标签条目 → 继续往后找，直到命中带标签的。"""
    write_json(tmp_path / "recommend_cache" / "a.json",
               {"tracks": [{"guid": GUID, "title": "", "artist": ""}]})
    write_json(tmp_path / "nm_cache" / "b.json",
               {"tracks": [{"guid": GUID, "title": "网易歌单", "artist": "A"}]})
    assert p._lookup_playlist_cache_track(GUID)["title"] == "网易歌单"


def test_playlist_cache_track_sees_new_file_immediately(tmp_path):
    assert p._lookup_playlist_cache_track(GUID) is None
    write_json(tmp_path / "recommend_cache" / "u1.json",
               {"tracks": [{"guid": GUID, "title": "刚下发", "artist": "A"}]})
    assert p._lookup_playlist_cache_track(GUID)["title"] == "刚下发"


# ------------------------------------------------------------------ P2：攒块写

@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
async def test_flush_disk_writes_bytes_and_clears_buffer(tmp_path):
    buf = bytearray(b"abc" * 1000)
    target = tmp_path / "x.bin"
    with open(target, "wb") as fp:
        await p._flush_disk(fp, buf)
        assert buf == bytearray(), "写完必须清空缓冲，否则字节会被重复写"
    assert target.read_bytes() == b"abc" * 1000


@pytest.mark.anyio
async def test_flush_disk_empty_buffer_is_noop(tmp_path):
    target = tmp_path / "x.bin"
    with open(target, "wb") as fp:
        await p._flush_disk(fp, bytearray())
    assert target.read_bytes() == b""


class ChunkedAudio(httpx.AsyncByteStream):
    def __init__(self, chunks):
        self.chunks = chunks

    async def __aiter__(self):
        for chunk in self.chunks:
            yield chunk

    async def aclose(self):
        pass


@pytest.mark.anyio
async def test_tee_large_stream_lands_byte_exact(tmp_path, monkeypatch):
    """>1MB 的流会走「攒块 + 线程写」多次 flush：字节内容与顺序必须完全一致。"""
    chunk = bytes(range(256)) * 512        # 128 KiB
    chunks = [chunk] * 9                   # 1.125 MiB → 中途至少 flush 一次
    total = sum(len(c) for c in chunks)
    assert total > p._DISK_FLUSH_BYTES

    captured: dict = {}

    def fake_finalize(part, guid, ext, info, tee_enabled):
        captured["data"] = Path(part).read_bytes()
        os.remove(part)
        return {}

    monkeypatch.setattr(p, "_tee_finalize", fake_finalize)
    monkeypatch.setitem(p.CONF, "tee_save_enabled", True)
    monkeypatch.setitem(p.CONF, "tee_save_dir", str(tmp_path / "library"))
    os.makedirs(p.CONF["tee_save_dir"], exist_ok=True)

    response = httpx.Response(
        200, stream=ChunkedAudio(chunks), headers={"content-length": str(total)},
    )
    tee = p.stream_tee_response(
        response, GUID, None, pre_info={"title": "T", "artist": "A"}, resolved_ext="flac",
    )
    body = b"".join([c async for c in tee.body_iterator])
    assert body == chunk * 9
    assert captured["data"] == chunk * 9


@pytest.mark.anyio
async def test_flush_disk_delegates_write_to_worker_thread(tmp_path, monkeypatch):
    """写盘必须离开事件循环（下载期间不能阻塞其它请求）。"""
    seen: list = []
    real_to_thread = asyncio.to_thread

    def spy(func, *args, **kwargs):
        seen.append(func)
        return real_to_thread(func, *args, **kwargs)

    monkeypatch.setattr(p.asyncio, "to_thread", spy)

    buf = bytearray(b"x" * 16)
    target = tmp_path / "y.bin"
    with open(target, "wb") as fp:
        await p._flush_disk(fp, buf)
    assert target.read_bytes() == b"x" * 16
    assert [getattr(f, "__name__", "") for f in seen] == ["write"]
