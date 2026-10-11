"""平台排行榜（榜单）→ 虚拟歌单注入。

与 platform_playlists.py（发现·歌单广场）互补：歌单广场给的是「按平台/分类的歌单」，
本模块给的是「平台的排行榜」（QQ 巅峰榜/地区榜/特色榜/全球榜、网易各类榜单）。
复用其曲目映射（`_map_tx_song`/`_map_wy_song`）与请求助手（`_get`），不重造轮子。

对外契约：
  1. `fetch_platform_charts(client, source)` → [{id, name, group}]（设置页多选渲染）；
  2. `fetch_chart_tracks(client, source, chart_id, limit)` → 在线条目统一形状。

曲目 id 同为洛雪形态 `lx:<platform>:<identifier>`，播放链与歌单广场完全一致
（app.py `_open_online_stream` 走 `online:lx:<platform>:<id>`）。

只读、无状态：所有函数失败一律返回空列表，绝不抛出到调用方。
"""
from __future__ import annotations

import json
import logging
from urllib.parse import quote

import httpx

try:
    from . import platform_playlists as plpl
except ImportError:  # uvicorn --app-dir proxy
    import platform_playlists as plpl  # type: ignore

logger = logging.getLogger("fnmusic_proxy.platform_charts")

# 支持榜单的平台（咪咕无榜单体系）。
# tx/wy 走平台自有榜单接口；kg/kw 走 lxserver 统一榜单通道（见 _lx_charts）。
CHART_PLATFORMS = ("tx", "wy", "kg", "kw")

# 走 lxserver 榜单通道的平台（需传入 lxmusic-service 的客户端）
_LX_CHART_SOURCES = ("kg", "kw")

# 兜底榜单表：动态拉取失败时保证设置页不空（id 由平台维护，可能变动）。
# tx topId / wy toplist id 均为实测（2026-10-11）。
PLATFORM_CHART_FALLBACK: dict[str, list[dict[str, str]]] = {
    "tx": [
        {"id": "62", "name": "飙升榜", "group": "巅峰榜"},
        {"id": "26", "name": "热歌榜", "group": "巅峰榜"},
        {"id": "27", "name": "新歌榜", "group": "巅峰榜"},
        {"id": "4", "name": "流行指数榜", "group": "巅峰榜"},
        {"id": "67", "name": "听歌识曲榜", "group": "巅峰榜"},
        {"id": "201", "name": "MV榜", "group": "巅峰榜"},
        {"id": "5", "name": "内地榜", "group": "地区榜"},
        {"id": "59", "name": "香港地区榜", "group": "地区榜"},
        {"id": "61", "name": "台湾地区榜", "group": "地区榜"},
        {"id": "3", "name": "欧美榜", "group": "地区榜"},
        {"id": "16", "name": "韩国榜", "group": "地区榜"},
        {"id": "17", "name": "日本榜", "group": "地区榜"},
        {"id": "126", "name": "JOOX本地热播榜", "group": "地区榜"},
        {"id": "130", "name": "香港TVB劲歌金榜", "group": "地区榜"},
        {"id": "127", "name": "台湾KKBOX榜", "group": "地区榜"},
        {"id": "58", "name": "说唱榜", "group": "特色榜"},
        {"id": "57", "name": "电音榜", "group": "特色榜"},
        {"id": "73", "name": "游戏音乐榜", "group": "特色榜"},
        {"id": "72", "name": "动漫音乐榜", "group": "特色榜"},
        {"id": "29", "name": "影视金曲榜", "group": "特色榜"},
        {"id": "64", "name": "综艺新歌榜", "group": "特色榜"},
        {"id": "65", "name": "国风热歌榜", "group": "特色榜"},
        {"id": "36", "name": "K歌金曲榜", "group": "特色榜"},
        {"id": "60", "name": "抖音热歌榜", "group": "特色榜"},
        {"id": "63", "name": "DJ舞曲榜", "group": "特色榜"},
        {"id": "28", "name": "网络歌曲榜", "group": "特色榜"},
        {"id": "108", "name": "美国公告牌榜", "group": "全球榜"},
        {"id": "129", "name": "韩国Melon榜", "group": "全球榜"},
        {"id": "107", "name": "英国UK榜", "group": "全球榜"},
        {"id": "105", "name": "日本公信榜", "group": "全球榜"},
    ],
    "wy": [
        {"id": "19723756", "name": "飙升榜", "group": "官方榜"},
        {"id": "3779629", "name": "新歌榜", "group": "官方榜"},
        {"id": "2884035", "name": "原创榜", "group": "官方榜"},
        {"id": "3778678", "name": "热歌榜", "group": "官方榜"},
        {"id": "1978921795", "name": "网易云电音榜", "group": "官方榜"},
        {"id": "991319590", "name": "中文说唱榜", "group": "官方榜"},
        {"id": "13372522766", "name": "潮流风向榜", "group": "官方榜"},
        {"id": "18176153161", "name": "实时分享榜", "group": "官方榜"},
        {"id": "71385702", "name": "网易云ACG榜", "group": "曲风榜"},
        {"id": "5059642708", "name": "网易云国风榜", "group": "曲风榜"},
        {"id": "5059661515", "name": "网易云民谣榜", "group": "曲风榜"},
        {"id": "71384707", "name": "网易云古典榜", "group": "曲风榜"},
        {"id": "5453912201", "name": "黑胶VIP爱听榜", "group": "会员榜"},
        {"id": "7785123708", "name": "黑胶VIP新歌榜", "group": "会员榜"},
        {"id": "7785066739", "name": "黑胶VIP热歌榜", "group": "会员榜"},
        {"id": "6688069460", "name": "听歌识曲榜", "group": "官方榜"},
        {"id": "60198", "name": "美国Billboard榜", "group": "全球榜"},
        {"id": "180106", "name": "英国UK排行榜周榜", "group": "全球榜"},
        {"id": "60131", "name": "日本Oricon榜", "group": "全球榜"},
    ],
}


def _clean_chart(rows: list, group_default: str = "榜单") -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    seen: set[str] = set()
    for it in rows or []:
        if not isinstance(it, dict):
            continue
        cid = str(it.get("id") or "").strip()
        name = plpl.clean_html(it.get("name"))
        if not cid or not name or cid in seen:
            continue
        seen.add(cid)
        out.append({"id": cid, "name": name,
                    "group": plpl.clean_html(it.get("group")) or group_default,
                    "cover": str(it.get("cover") or "").strip()})
    return out


# ------------------------------------------------------------- 榜单列表 --

async def _tx_charts(client: httpx.AsyncClient) -> list[dict[str, str]]:
    """QQ 全部排行榜（musicu.fcg `musicToplist.ToplistInfoServer/GetAll`）。

    返回 `topList.data.group[]`（巅峰榜/地区榜/特色榜/全球榜），每项 `topId`+`title`。
    GET + `data=<urlencoded json>`（不是 POST）。
    """
    payload = {
        "comm": {"ct": 24, "cv": 0},
        "topList": {"module": "musicToplist.ToplistInfoServer", "method": "GetAll", "param": {}},
    }
    url = ("https://u.y.qq.com/cgi-bin/musicu.fcg?format=json&data="
           + quote(json.dumps(payload, ensure_ascii=False), safe=""))
    data = await plpl._get(client, url, plpl._TX_HEADERS)
    groups = (data or {}).get("topList", {}).get("data", {}).get("group") if isinstance(data, dict) else None
    if not isinstance(groups, list):
        return []
    out: list[dict[str, str]] = []
    for g in groups:
        if not isinstance(g, dict):
            continue
        gname = plpl.clean_html(g.get("groupName")) or "榜单"
        for t in g.get("toplist") or []:
            if not isinstance(t, dict):
                continue
            tid = t.get("topId")
            name = plpl.clean_html(t.get("title") or t.get("name"))
            if tid and name:
                out.append({"id": str(tid), "name": name, "group": gname,
                            "cover": str(t.get("picUrl") or t.get("pic_url") or "")})
    return out


async def _wy_charts(client: httpx.AsyncClient) -> list[dict[str, str]]:
    """网易全部榜单（`/api/toplist`）：`list[]` 每项含 id/name。"""
    data = await plpl._get(client, "https://music.163.com/api/toplist", plpl._WY_HEADERS)
    lst = (data or {}).get("list") if isinstance(data, dict) else None
    if not isinstance(lst, list):
        return []
    return _clean_chart(
        [{"id": it.get("id"), "name": it.get("name"),
          "cover": it.get("coverImgUrl") or it.get("picUrl") or ""}
         for it in lst if isinstance(it, dict)],
        group_default="榜单",
    )


def _lx_url(lx_base: str, path: str) -> str:
    """拼 lxmusic-service 绝对地址（代理/WebUI 用的客户端无 base_url，必须绝对 URL）。"""
    base = str(lx_base or "").strip().rstrip("/")
    return (base + path) if base else path


async def _lx_charts(client: httpx.AsyncClient, source: str, lx_base: str = "") -> list[dict[str, str]]:
    """kg/kw 榜单列表：走 lxmusic-service `/api/v1/charts/boards`（透传 lxserver boards）。"""
    try:
        r = await client.get(_lx_url(lx_base, "/api/v1/charts/boards"),
                             params={"source": source}, timeout=15.0)
        if r.status_code != 200:
            logger.debug("lx charts boards %s http %s", source, r.status_code)
            return []
        data = r.json()
    except Exception as exc:  # noqa: BLE001
        logger.debug("lx charts boards %s failed: %s", source, exc)
        return []
    boards = data.get("boards") if isinstance(data, dict) else None
    if not isinstance(boards, list):
        return []
    out: list[dict[str, str]] = []
    for b in boards:
        if not isinstance(b, dict):
            continue
        bid = str(b.get("id") or "").strip()
        name = plpl.clean_html(b.get("name"))
        if bid and name:
            out.append({"id": bid, "name": name, "group": "榜单", "cover": ""})
    return out


async def _lx_chart_tracks(client: httpx.AsyncClient, source: str, chart_id: str,
                           limit: int, lx_base: str = "") -> list[dict]:
    """kg/kw 榜单曲目：走 lxmusic-service `/api/v1/charts/list`（透传 lxserver list）。"""
    try:
        r = await client.get(
            _lx_url(lx_base, "/api/v1/charts/list"),
            params={"source": source, "bangid": chart_id, "limit": limit},
            timeout=20.0,
        )
        if r.status_code != 200:
            logger.debug("lx charts list %s/%s http %s", source, chart_id, r.status_code)
            return []
        data = r.json()
    except Exception as exc:  # noqa: BLE001
        logger.debug("lx charts list %s/%s failed: %s", source, chart_id, exc)
        return []
    items = data.get("items") if isinstance(data, dict) else None
    if not isinstance(items, list):
        return []
    out: list[dict] = []
    for it in items:
        if not isinstance(it, dict):
            continue
        sid = str(it.get("id") or "").strip()
        if not sid:
            continue
        out.append({
            "id": sid,
            "source": "lx",
            "lx_source": str(it.get("lx_source") or source),
            "title": plpl.clean_html(it.get("title") or it.get("name")),
            "artist": plpl.clean_html(it.get("artist")),
            "album": plpl.clean_html(it.get("album")),
            "duration_s": float(it.get("duration_s") or 0),
            "ext": str(it.get("ext") or "mp3"),
            "cover_url": str(it.get("cover_url") or ""),
        })
    return out[:limit]


async def fetch_platform_charts(
    client: httpx.AsyncClient, source: str, lx_base: str = "",
) -> list[dict[str, str]]:
    """某平台的全部榜单 [{id, name, group}]；不支持/失败返回内置兜底表。

    tx 走 musicu.fcg GetAll（权威、动态）；wy 走 /api/toplist。一律不抛异常——
    UI 拿到空列表即隐藏该平台。兜底表保证上游抖动时设置页仍有可选项。
    """
    src = str(source or "").strip()
    if src not in CHART_PLATFORMS:
        return []
    fallback = list(PLATFORM_CHART_FALLBACK.get(src) or [])
    try:
        if src == "tx":
            rows = await _tx_charts(client)
        elif src == "wy":
            rows = await _wy_charts(client)
        elif src in _LX_CHART_SOURCES:
            rows = await _lx_charts(client, src, lx_base)
        else:
            rows = []
        if rows:
            return rows
    except Exception as exc:  # noqa: BLE001
        logger.debug("fetch_platform_charts %s failed: %s", source, exc)
    return fallback


# ------------------------------------------------------------- 榜单曲目 --

async def _tx_chart_tracks(client: httpx.AsyncClient, chart_id: str, limit: int) -> list[dict]:
    """QQ 榜单曲目：`GetDetail(topId, offset, num)` → `songInfoList[]`。"""
    try:
        top_id = int(str(chart_id).strip())
    except (TypeError, ValueError):
        return []
    out: list[dict] = []
    pages = 0
    while len(out) < limit and pages < 6:
        payload = {
            "comm": {"ct": 24, "cv": 0},
            "topList": {"module": "musicToplist.ToplistInfoServer", "method": "GetDetail",
                        "param": {"topId": top_id, "offset": len(out), "num": min(30, limit), "period": ""}},
        }
        url = ("https://u.y.qq.com/cgi-bin/musicu.fcg?format=json&data="
               + quote(json.dumps(payload, ensure_ascii=False), safe=""))
        data = await plpl._get(client, url, plpl._TX_HEADERS)
        rows = (data or {}).get("topList", {}).get("data", {}).get("songInfoList") if isinstance(data, dict) else None
        if not isinstance(rows, list) or not rows:
            break
        for s in rows:
            if not isinstance(s, dict):
                continue
            si = s.get("songInfo") or s.get("songData") or s
            t = plpl._map_tx_song(si)
            if t:
                out.append(t)
        pages += 1
    return out[:limit]


async def fetch_chart_tracks(
    client: httpx.AsyncClient, source: str, chart_id: str, limit: int = 100,
    lx_base: str = "",
) -> list[dict]:
    """单个榜单的曲目（在线条目统一形状）；失败返回 []。

    tx 走 GetDetail；wy 的 toplist id 本质就是歌单 id → 直接复用歌单广场的
    `fetch_playlist_tracks("wy", ...)`（`/api/playlist/detail`）。
    """
    src = str(source or "").strip()
    cid = str(chart_id or "").strip()
    if not cid:
        return []
    limit = max(1, min(int(limit or 100), 300))
    try:
        if src == "tx":
            return await _tx_chart_tracks(client, cid, limit)
        if src == "wy":
            return await plpl.fetch_playlist_tracks(client, "wy", cid, limit)
        if src in _LX_CHART_SOURCES:
            return await _lx_chart_tracks(client, src, cid, limit, lx_base)
    except Exception as exc:  # noqa: BLE001
        logger.debug("fetch_chart_tracks %s/%s failed: %s", src, cid, exc)
    return []
