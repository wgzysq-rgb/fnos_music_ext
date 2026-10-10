"""曲库探测：配置的 music.db 不可用时按宿主机目录布局兜底。

背景（真机事故）：应用中心 fpk 形态下代理跑在 NAS 宿主机的 systemd 服务里，
而 CONF["music_db"] 的默认值是官方音乐 App 的「容器内视角」路径
（/usr/local/apps/@appdata/trim.music/db/music.db），宿主机上并不存在 →
detect_library_dir() 静默回落到 cache 目录 → 边听边存仍然落盘，但文件名是
哈希、不进官方曲库，用户看到的就是「边听边存彻底失效、不知道歌存哪了」。

本文件锁定三点：
1. 显式 FNMUSIC_LIBRARY_DIR 优先；
2. 配置的 music.db 落空时按 _LIBRARY_DB_PATTERNS 探测宿主机真实 db，
   命中即用并留 INFO 痕迹；
3. 全部落空才回落 cache 目录，且必须打 WARNING（含 FNMUSIC_MUSIC_DB 提示）。
"""
from __future__ import annotations

import importlib
import logging
import sqlite3

import pytest

p = importlib.import_module("proxy.app")


def _write_db(path, dirs):
    """造一份带 shared_library 表的 music.db 桩。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(path))
    try:
        con.execute("CREATE TABLE shared_library (id INTEGER PRIMARY KEY, path TEXT)")
        for i, d in enumerate(dirs, 1):
            con.execute("INSERT INTO shared_library (id, path) VALUES (?, ?)", (i, str(d)))
        con.commit()
    finally:
        con.close()


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setitem(p.CONF, "library_dir", "")
    monkeypatch.setitem(p.CONF, "music_db", str(tmp_path / "missing.db"))
    monkeypatch.setitem(p.CONF, "cache_dir", str(tmp_path / "cache"))
    monkeypatch.setitem(p.CONF, "tee_save_dir", "")
    monkeypatch.setattr(p, "_LIBRARY_DB_PATTERNS", ())
    monkeypatch.setattr(p, "_LIBRARY_FALLBACK_WARNED", False)
    monkeypatch.setattr(p, "_MUSIC_DB_RESOLVED_LOGGED", False)
    monkeypatch.setattr(p, "_MUSIC_DB_CACHE", None)
    yield


# ------------------------------------------------------------- 优先级 --

def test_explicit_library_dir_wins(tmp_path, monkeypatch):
    target = tmp_path / "my_library"
    target.mkdir()
    monkeypatch.setitem(p.CONF, "library_dir", str(target))
    _write_db(tmp_path / "other.db", [tmp_path / "ignored"])
    monkeypatch.setitem(p.CONF, "music_db", str(tmp_path / "other.db"))
    assert p.detect_library_dir() == str(target)


def test_configured_db_is_used_when_present(tmp_path, monkeypatch):
    lib = tmp_path / "shared"
    lib.mkdir()
    db = tmp_path / "music.db"
    _write_db(db, [lib])
    monkeypatch.setitem(p.CONF, "music_db", str(db))
    assert p.detect_library_dir() == str(lib)


def test_first_existing_row_wins(tmp_path, monkeypatch):
    missing = tmp_path / "gone"
    ok = tmp_path / "ok"
    ok.mkdir()
    db = tmp_path / "music.db"
    _write_db(db, [missing, ok])
    monkeypatch.setitem(p.CONF, "music_db", str(db))
    assert p.detect_library_dir() == str(ok)


# ------------------------------------------- 宿主机布局兜底（本次修复核心）--

def test_host_layout_fallback_finds_db(tmp_path, monkeypatch, caplog):
    lib = tmp_path / "vol1/@appdata/trim.music/library"
    lib.mkdir(parents=True)
    host_db = tmp_path / "vol1/@appdata/trim.music/db/music.db"
    _write_db(host_db, [lib])
    monkeypatch.setattr(p, "_LIBRARY_DB_PATTERNS", (str(host_db),))

    with caplog.at_level(logging.INFO, logger="fnmusic_proxy"):
        # 配置值（容器内视角路径）不存在，必须能靠目录布局兜底找到真实 db
        assert p.detect_library_dir() == str(lib)

    assert "曲库自动探测命中" in caplog.text
    assert "FNMUSIC_MUSIC_DB" in caplog.text


def test_fallback_info_logged_only_once(tmp_path, monkeypatch, caplog):
    lib = tmp_path / "shared"
    lib.mkdir()
    host_db = tmp_path / "vol1/@appdata/trim.music/db/music.db"
    _write_db(host_db, [lib])
    monkeypatch.setattr(p, "_LIBRARY_DB_PATTERNS", (str(host_db),))

    with caplog.at_level(logging.INFO, logger="fnmusic_proxy"):
        p.detect_library_dir()
        p.detect_library_dir()
        p.detect_library_dir()
    assert caplog.text.count("曲库自动探测命中") == 1


def test_iter_candidates_prefers_configured_and_filters_missing(tmp_path, monkeypatch):
    other = tmp_path / "discovered.db"
    _write_db(other, [tmp_path])
    monkeypatch.setattr(p, "_LIBRARY_DB_PATTERNS", (str(tmp_path / "missing.db"), str(other)))
    # 配置值指向缺失文件 → 被过滤；只剩探测到的那个
    assert p.iter_music_db_candidates() == [str(other)]

    configured = tmp_path / "configured.db"
    _write_db(configured, [tmp_path])
    p.CONF["music_db"] = str(configured)
    assert p.iter_music_db_candidates() == [str(configured), str(other)]


# ------------------------------------------------- 全部落空 → 回落 cache --

def test_all_missing_falls_back_to_cache_with_warning(caplog):
    with caplog.at_level(logging.WARNING, logger="fnmusic_proxy"):
        assert p.detect_library_dir() == p.CONF["cache_dir"]
    assert "曲库探测失败" in caplog.text
    assert "FNMUSIC_MUSIC_DB" in caplog.text
    assert p.CONF["cache_dir"] in caplog.text


def test_fallback_warning_only_once(caplog):
    with caplog.at_level(logging.WARNING, logger="fnmusic_proxy"):
        p.detect_library_dir()
        p.detect_library_dir()
    assert caplog.text.count("曲库探测失败") == 1


def test_rows_without_existing_dir_warns_and_falls_back(tmp_path, monkeypatch, caplog):
    db = tmp_path / "music.db"
    _write_db(db, [tmp_path / "not-mounted"])
    monkeypatch.setitem(p.CONF, "music_db", str(db))
    with caplog.at_level(logging.WARNING, logger="fnmusic_proxy"):
        assert p.detect_library_dir() == p.CONF["cache_dir"]
    assert "均不存在/不可访问" in caplog.text


def test_unreadable_db_is_tolerated(tmp_path, monkeypatch, caplog):
    """不是 sqlite 文件（表缺失）也不能炸：继续找下一个候选，最后回落 cache。"""
    broken = tmp_path / "broken.db"
    broken.write_bytes(b"not a database")
    monkeypatch.setitem(p.CONF, "music_db", str(broken))
    with caplog.at_level(logging.WARNING, logger="fnmusic_proxy"):
        assert p.detect_library_dir() == p.CONF["cache_dir"]
    assert "Failed to read shared_library path" in caplog.text


# ------------------------------------------------------- resolve_music_db --

def test_resolve_music_db_prefers_configured(tmp_path, monkeypatch):
    configured = tmp_path / "configured.db"
    _write_db(configured, [tmp_path])
    _write_db(tmp_path / "found.db", [tmp_path])
    monkeypatch.setitem(p.CONF, "music_db", str(configured))
    monkeypatch.setattr(p, "_LIBRARY_DB_PATTERNS", (str(tmp_path / "found.db"),))
    assert p.resolve_music_db() == str(configured)


def test_resolve_music_db_falls_back_to_discovered(tmp_path, monkeypatch):
    found = tmp_path / "found.db"
    _write_db(found, [tmp_path])
    monkeypatch.setattr(p, "_LIBRARY_DB_PATTERNS", (str(found),))
    assert p.resolve_music_db() == str(found)


def test_resolve_music_db_returns_configured_when_nothing_found(tmp_path, monkeypatch):
    monkeypatch.setitem(p.CONF, "music_db", str(tmp_path / "missing.db"))
    assert p.resolve_music_db() == str(tmp_path / "missing.db")


# ----------------------------------------------------- 启动自检留痕 --

@pytest.mark.anyio
async def test_startup_selfcheck_logs_resolved_dirs(caplog):
    """启动日志必须直接给出「边听边存会落到哪」，否则用户无处自查。"""
    with caplog.at_level(logging.INFO, logger="fnmusic_proxy"):
        async with p.lifespan(p.app):
            pass
    assert "[自检] 曲库目录" in caplog.text
    assert "[自检] 边听边存落盘目录" in caplog.text
