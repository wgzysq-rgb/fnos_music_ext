"""在线曲目元数据登记表 —— 修复"推荐歌单曲目播放时显示未知曲目/未知歌手"。

## 问题链路

飞牛播放器打开曲目详情时请求 `/music/api/v1/track/metadata`，proxy 的
`_online_info()` 依次尝试：

1. `_retained_track()` —— 只查 `_SEARCH_CACHE`，**只有搜索结果在里面**；
2. `_fetch_online_info()` —— 转发到对应音源后端取详情。

推荐歌单（daily / hot / discover）与网易账号歌单的曲目**从未走过"搜索"**，因此：

- (1) 必然落空；
- (2) 对 lx 源会打到 lxmusic-service 的 `/api/v1/track/info`，而该接口只对
  **自己搜索过的**曲目有缓存，未命中时用 `lxserver_client.synthesize_song_info()`
  合成占位值 —— 即 `未知曲目` / `未知歌手`，`albumName` 为空。

用户在播放器里看到的正是这两个常量（洛雪的兜底文案，不是飞牛客户端的 bug）。

## 本模块的作用

这些歌单在**下发曲目列表时**把真实元数据登记进来；`_online_info()` 拿到上游结果后，
用登记值**替换占位值**（绝不覆盖上游的真实数据，避免影响搜索结果等既有链路）。

**只回填展示字段（title / artist / album / cover_url）**。时长 `duration_s` 虽登记
但不回填 —— 它会流入 `transcode` 的 HLS 分片数计算，而歌单标注时长与实际播放音频
（洛雪音源解析）可能不一致，回填会破坏「声明分片数 ≤ 实际分片数」的不变量，
表现为播放时整首跳下一首。详见 `OnlineTrackRegistry.apply()` 的说明。

## 存储

内存 dict（热路径）+ 磁盘 JSON（跨重启；推荐歌单本身也按天持久化）。
落盘走节流，避免每次下发都写盘。
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from typing import Any, Iterable

logger = logging.getLogger(__name__)

# 上游"没取到元数据"时会出现的占位值（lxserver synthesize_song_info 的兜底常量，
# 以及飞牛前端自身的兜底文案）。登记值只在命中这些值时用于覆盖。
PLACEHOLDER_VALUES: frozenset[str] = frozenset({
    "", "未知曲目", "未知歌手", "未知专辑", "未知艺术家",
    "unknown", "Unknown", "UNKNOWN", "未知",
})

_MAX_ENTRIES = 8000          # 容量上限，超出淘汰最久未访问
_TTL_S = 14 * 86400.0        # 14 天（推荐歌单按天轮换，14 天足够覆盖）
_FLUSH_MIN_INTERVAL_S = 10.0  # 落盘节流：两次写盘最小间隔


def _index_dir() -> str:
    """索引落盘目录：与推荐歌单缓存同处一处（随 FNMUSIC_RECOMMEND_DIR 走）。"""
    env = (os.environ.get("FNMUSIC_RECOMMEND_DIR") or "").strip()
    if env:
        return env
    home = (os.environ.get("FNMUSIC_HOME") or "").strip()
    if not home:
        home = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    return os.path.join(home, "recommend_cache")


def _index_path() -> str:
    return os.path.join(_index_dir(), "online-track-index.json")


def _is_placeholder(value: Any) -> bool:
    if value is None:
        return True
    text = str(value).strip()
    return text in PLACEHOLDER_VALUES


def is_placeholder(value: Any) -> bool:
    """上游占位值判定（公开出口）。

    落盘链路（曲库定名/写标签、下载产物打标签）必须用它判断"元数据是否真的可用"：
    只判空串会把 lxserver 合成的 `未知曲目/未知歌手` 当成有效值写进曲库与文件标签。
    """
    return _is_placeholder(value)


# 展示名/文件名按 "艺术家 - 标题" 约定拼接，占位片段逐段判定
_NAME_SPLIT_RE = re.compile(r"\s+-\s+")
# 历史遗留（issue #28）：文件以 "unknown (2)" 这类编号名落库
_LEGACY_UNKNOWN_RE = re.compile(r"unknown( \(\d+\))?", re.IGNORECASE)


def is_placeholder_name(name: Any) -> bool:
    """文件名/展示名是否为上游占位式（"未知歌手 - 未知曲目" / "unknown" / "unknown (2)"）。

    用于存量文件识别与下载自愈：判断"这个名字是不是上游兜底文案"。按 " - " 分词后
    逐段比占位常量（并兼容历史 unknown 编号名），guid 哈希名与正常曲名都不算命中。
    """
    text = _clean(name)
    if not text:
        return False
    for part in _NAME_SPLIT_RE.split(text):
        part = part.strip()
        if part and (_is_placeholder(part) or _LEGACY_UNKNOWN_RE.fullmatch(part)):
            return True
    return False


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _to_float(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


# 与本表只登记"在线曲目"有关：daily/hot 歌单会混入本地曲库兜底条目，
# 那些不需要（也不应该）登记。前缀常量与 app.is_online_guid 保持一致。
ONLINE_GUID_PREFIX = "online:"


def meta_from_track_vo(vo: dict, guid: str = "") -> dict | None:
    """从在线曲目 VO（`build_online_track` 的产物）提取可登记的元数据。

    guid 为显式传入时优先（调用方通常已解析好，避免依赖 VO 的字段形态）。
    非在线 guid（本地曲目）一律不登记，返回 None。
    """
    if not isinstance(vo, dict):
        return None
    key = _clean(guid) or _clean(vo.get("guid")) or _clean(vo.get("id"))
    if not key or not key.startswith(ONLINE_GUID_PREFIX):
        return None
    album = vo.get("album")
    if isinstance(album, dict):
        album_name = _clean(album.get("name"))
    else:
        album_name = _clean(album)
    title = _clean(vo.get("title")) or _clean(vo.get("name"))
    artist = _clean(vo.get("artist"))
    meta = {
        "guid": key,
        "title": title,
        "artist": artist,
        "album": album_name or _clean(vo.get("albumName")),
        "duration_s": _to_float(vo.get("duration_s")),
        "ext": _clean(vo.get("ext")) or "mp3",
        "cover_url": _clean(vo.get("cover_url")) or _clean(vo.get("coverUrl")),
    }
    # 三个显示字段全空则无登记价值（避免把纯占位条目写进索引）
    if all(_is_placeholder(meta[f]) for f in ("title", "artist", "album")):
        return None
    return meta


class OnlineTrackRegistry:
    """guid → 在线曲目元数据。线程安全；磁盘持久化走节流。"""

    def __init__(self, path: str | None = None) -> None:
        self._lock = threading.Lock()
        self._entries: dict[str, dict] = {}
        self._loaded_path: str | None = None
        self._explicit_path = path
        self._dirty = False
        self._last_flush = 0.0

    # ------------------------------------------------------------ 路径与加载 --

    def _path(self) -> str:
        return self._explicit_path or _index_path()

    def _ensure_loaded(self) -> None:
        """懒加载磁盘索引；目录/文件缺失或损坏都静默降级为空索引。"""
        path = self._path()
        if self._loaded_path == path:
            return
        self._loaded_path = path
        try:
            with open(path, "r", encoding="utf-8") as fh:
                raw = json.load(fh)
        except (OSError, ValueError):
            return
        if not isinstance(raw, dict):
            return
        items = raw.get("items")
        if not isinstance(items, dict):
            return
        now = time.time()
        for guid, meta in items.items():
            if not isinstance(meta, dict):
                continue
            ts = _to_float(meta.get("ts"))
            if ts and now - ts > _TTL_S:
                continue
            meta = dict(meta)
            meta["guid"] = _clean(meta.get("guid")) or guid
            self._entries[guid] = meta
        if self._entries:
            logger.info("online_registry: 从磁盘加载 %d 条曲目元数据", len(self._entries))

    # -------------------------------------------------------------- 写入 --

    def record(self, meta: dict | None) -> bool:
        if not isinstance(meta, dict):
            return False
        guid = _clean(meta.get("guid"))
        if not guid:
            return False
        with self._lock:
            self._ensure_loaded()
            entry = {
                "guid": guid,
                "title": _clean(meta.get("title")),
                "artist": _clean(meta.get("artist")),
                "album": _clean(meta.get("album")),
                "duration_s": _to_float(meta.get("duration_s")),
                "ext": _clean(meta.get("ext")) or "mp3",
                "cover_url": _clean(meta.get("cover_url")),
                "ts": time.time(),
            }
            self._entries[guid] = entry
            self._evict_locked()
            self._dirty = True
            self._maybe_flush_locked()
        return True

    def record_many(self, metas: Iterable[dict]) -> int:
        """批量登记；返回实际登记条数。"""
        count = 0
        with self._lock:
            self._ensure_loaded()
            now = time.time()
            for meta in metas:
                if not isinstance(meta, dict):
                    continue
                guid = _clean(meta.get("guid"))
                if not guid:
                    continue
                self._entries[guid] = {
                    "guid": guid,
                    "title": _clean(meta.get("title")),
                    "artist": _clean(meta.get("artist")),
                    "album": _clean(meta.get("album")),
                    "duration_s": _to_float(meta.get("duration_s")),
                    "ext": _clean(meta.get("ext")) or "mp3",
                    "cover_url": _clean(meta.get("cover_url")),
                    "ts": now,
                }
                count += 1
            if count:
                self._evict_locked()
                self._dirty = True
                self._maybe_flush_locked()
        return count

    def record_tracks(self, tracks: Iterable[dict]) -> int:
        """从曲目 VO 列表批量登记（自动提取元数据）。"""
        return self.record_many(
            m for m in (meta_from_track_vo(t) for t in tracks) if m
        )

    def _evict_locked(self) -> None:
        if len(self._entries) <= _MAX_ENTRIES:
            return
        # 按访问时间升序淘汰最旧的一批（保留最近 3/4）
        ordered = sorted(self._entries.items(), key=lambda kv: _to_float(kv[1].get("ts")))
        drop = len(self._entries) - int(_MAX_ENTRIES * 0.75)
        for guid, _ in ordered[:max(drop, 0)]:
            self._entries.pop(guid, None)

    def _maybe_flush_locked(self) -> None:
        now = time.monotonic()
        if now - self._last_flush < _FLUSH_MIN_INTERVAL_S:
            return
        self._flush_locked()

    def flush(self, force: bool = True) -> bool:
        """显式落盘（测试与优雅退出用）。"""
        with self._lock:
            if not force and not self._dirty:
                return False
            return self._flush_locked()

    def _flush_locked(self) -> bool:
        path = self._path()
        try:
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
            tmp = path + ".tmp"
            payload = {"version": 1, "ts": time.time(), "items": self._entries}
            with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
                json.dump(payload, fh, ensure_ascii=False)
            os.replace(tmp, path)
        except OSError as exc:
            logger.warning("online_registry: 落盘失败 %s: %s", path, exc)
            return False
        self._dirty = False
        self._last_flush = time.monotonic()
        return True

    # -------------------------------------------------------------- 读取 --

    def lookup(self, guid: str) -> dict | None:
        key = _clean(guid)
        if not key:
            return None
        with self._lock:
            self._ensure_loaded()
            meta = self._entries.get(key)
            if not meta:
                return None
            if time.time() - _to_float(meta.get("ts")) > _TTL_S:
                self._entries.pop(key, None)
                return None
            meta = dict(meta)
            meta["ts"] = time.time()  # 命中即刷新访问时间
            self._entries[key] = meta
        return meta

    def snapshot(self) -> dict[str, dict]:
        """全量条目副本（诊断与存量修复脚本用）。

        热路径请继续用 lookup/apply：这里会复制整张表。超过 TTL 的条目不返回。
        """
        with self._lock:
            self._ensure_loaded()
            now = time.time()
            return {
                guid: dict(meta)
                for guid, meta in self._entries.items()
                if not (now - _to_float(meta.get("ts")) > _TTL_S)
            }

    def apply(self, guid: str, data: dict | None) -> dict | None:
        """用登记值回填上游数据中的占位/缺失字段，返回新的 dict。

        - title / artist / album：上游为空或命中占位常量时用登记值覆盖；
        - cover_url：上游缺失（空）时补登记值（仅展示用，不进播放链）；
        - 其余字段一律不动（不干扰播放链与既有搜索路径）。

        **duration_s 刻意不回填**（2026-10-09 实测回归）：
        歌单接口给的时长是「上游平台官方版」的时长，而实际播放的音频由洛雪
        音源解析，两者可能不一致（哪怕差几秒）。而 `transcode.Session` 用
        `floor(duration_s / hls_time)` 预合成 HLS 分片清单，其正确性依赖
        「声明分片数 ≤ ffmpeg 实际产出数」这一不变量 —— 见 transcode.py 开头
        「App 多要一片会 404」的说明。上游拿不到时长时 duration_s 为 0，
        `transcode.ensure_session()` 会用 `probe_duration()` 实测真实时长，
        不变量天然成立；一旦这里塞入估算值就会跳过实测，声明片数可能多于
        实际片数 → App 拉到 404 分片 → **整首直接跳下一首**。
        """
        meta = self.lookup(guid)
        if not meta:
            return data
        out = dict(data) if isinstance(data, dict) else {}
        for field in ("title", "artist", "album"):
            reg = _clean(meta.get(field))
            if reg and _is_placeholder(out.get(field)):
                out[field] = reg
        if not _clean(out.get("cover_url")) and meta.get("cover_url"):
            out["cover_url"] = meta["cover_url"]
        return out

    # -------------------------------------------------------------- 维护 --

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()
            self._dirty = True

    def stats(self) -> dict:
        with self._lock:
            self._ensure_loaded()
            return {"entries": len(self._entries), "path": self._path()}

    def reset_for_tests(self, path: str | None = None) -> None:
        with self._lock:
            self._entries.clear()
            self._dirty = False
            self._loaded_path = None
            self._last_flush = 0.0
            self._explicit_path = path


# 进程级单例
REGISTRY = OnlineTrackRegistry()
