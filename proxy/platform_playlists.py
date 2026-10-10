"""平台推荐歌单（发现·歌单广场）：把柠檬音乐「发现」页的推荐歌单能力移植到 fnmusic-ext。

设计定位（与 recommend.py 的每日/热门推荐互补）：
  - recommend.py 产出「一个歌单 = 一批按口味/榜单挑选的曲目」；
  - 本模块产出「按平台聚合的推荐歌单」，即柠檬「发现 → 歌单广场」能看到的
    各平台推荐歌单（QQ 歌单广场 / 网易 playlist/list / 酷我 getRcmPlayList /
    酷狗 getSpecial / 咪咕 playlist-square-recommend）。

对外契约：
  1. `fetch_platform_playlists(source, sort, page, limit)` 拉取某平台推荐歌单卡列表；
  2. `fetch_playlist_tracks(source, playlist_id, page, limit)` 拉取单个平台歌单的曲目；
  3. 两者都把上游原始结构映射为 fnmusic-ext 的「在线条目」统一形状
     （id / title / artist / album / duration_s / ext / cover_url / source），
     由 app.py 的 build_online_track() 转成飞牛客户端 Track VO。

曲目 id 采用**洛雪形态** `lx:<platform>:<identifier>`：本模块只负责「选什么曲」，
真正取播放直链仍由用户自建洛雪音源解析（app.py 的 `_open_online_stream` 走
`online:lx:<platform>:<id>` → lxmusic-service `/api/v1/track/url`），因此
identifier 必须满足 lxserver 契约（kg=hash / mg=copyrightId / 其余=songmid|rid）。

上游接口全部来自公开网页端 API（与柠檬 musicSdk.js 一致），不依赖洛雪源做歌单
列表查询——但曲目播放依赖洛雪源处于激活状态（与搜索栏在线结果同款前提）。

只读、无状态：所有函数失败一律返回空列表/None，绝不抛出到调用方。
"""
from __future__ import annotations

import html
import json
import logging
import re
from typing import Any
from urllib.parse import quote

import httpx

logger = logging.getLogger("fnmusic_proxy.platform_playlists")

# 与柠檬 musicSdk.js 的 AVAILABLE_SOURCES 对齐（平台代码同 lx 契约 kg/wy/tx/kw/mg）
SUPPORTED_PLATFORMS = ("tx", "wy", "kw", "kg", "mg")

PLATFORM_LABELS = {
    "tx": "QQ音乐",
    "wy": "网易云",
    "kw": "酷我",
    "kg": "酷狗",
    "mg": "咪咕",
}

# 排序维度（各平台语义见各 fetcher；不支持的平台忽略该参数）
SORT_OPTIONS = (
    {"id": "hot", "label": "最热"},
    {"id": "new", "label": "最新"},
    {"id": "recommend", "label": "推荐"},
)

# 歌单分类（2026-10 实测各平台接口后固化）。
#
# 各平台分类体系与取值形态差异很大，故这里只统一「对外形状」：
#   {"id": "<平台内取值>", "name": "<显示名>", "group": "<分组名>"}
# 其中 id 对 tx/kg/kw 是平台分类数字 id，对 wy 是**中文分类名**（其 catlist
# 接口已下线，cat 参数只认中文名）。咪咕无分类体系，不在表内。
#
# 数据来源（均为实测可用的公开网页端接口）：
#   - tx: c.y.qq.com/splcloud/fcgi-bin/fcg_get_diss_tag_conf.fcg（权威分类树）
#   - kg: mobilecdnbj.kugou.com/api/v3/tag/list?pid=0（父分类，取歌单需再查 tag/info）
#   - kw: wapi.kuwo.cn/api/pc/classify/playlist/getTagList（含 digest）
#   - wy: music.163.com/api/playlist/list?cat=<中文名>（catlist 已 404，这里固化常用项）
#
# 注意：这些 id 由平台维护，可能变动。app.py 侧的分类接口优先走**动态拉取**
# （fetch_platform_categories），本表是动态拉取失败时的兜底，保证 UI 不空。
CATEGORY_GROUP_FALLBACK: dict[str, list[dict[str, str]]] = {
    "tx": [
        {"id": "165", "name": "国语", "group": "语种"},
        {"id": "167", "name": "英语", "group": "语种"},
        {"id": "168", "name": "韩语", "group": "语种"},
        {"id": "166", "name": "粤语", "group": "语种"},
        {"id": "169", "name": "日语", "group": "语种"},
        {"id": "170", "name": "小语种", "group": "语种"},
        {"id": "6", "name": "流行", "group": "流派"},
        {"id": "15", "name": "轻音乐", "group": "流派"},
        {"id": "11", "name": "摇滚", "group": "流派"},
        {"id": "28", "name": "民谣", "group": "流派"},
        {"id": "153", "name": "嘻哈", "group": "流派"},
        {"id": "24", "name": "电子", "group": "流派"},
        {"id": "27", "name": "古典", "group": "流派"},
        {"id": "21", "name": "爵士", "group": "流派"},
        {"id": "39", "name": "ACG", "group": "主题"},
        {"id": "136", "name": "经典", "group": "主题"},
        {"id": "133", "name": "影视", "group": "主题"},
        {"id": "131", "name": "儿歌", "group": "主题"},
        {"id": "145", "name": "中国风", "group": "主题"},
        {"id": "194", "name": "古风", "group": "主题"},
        {"id": "148", "name": "情歌", "group": "主题"},
        {"id": "14", "name": "DJ", "group": "主题"},
        {"id": "52", "name": "伤感", "group": "心情"},
        {"id": "122", "name": "安静", "group": "心情"},
        {"id": "116", "name": "治愈", "group": "心情"},
        {"id": "125", "name": "励志", "group": "心情"},
        {"id": "78", "name": "睡前", "group": "场景"},
        {"id": "101", "name": "学习", "group": "场景"},
        {"id": "99", "name": "运动", "group": "场景"},
        {"id": "85", "name": "开车", "group": "场景"},
        {"id": "94", "name": "工作", "group": "场景"},
        {"id": "81", "name": "旅行", "group": "场景"},
    ],
    "kg": [
        {"id": "153", "name": "专区", "group": "分类"},
        {"id": "2005", "name": "心情", "group": "分类"},
        {"id": "1263", "name": "特色", "group": "分类"},
        {"id": "2003", "name": "场景", "group": "分类"},
        {"id": "2007", "name": "主题", "group": "分类"},
        {"id": "2009", "name": "语言", "group": "分类"},
        {"id": "1599", "name": "风格", "group": "分类"},
        {"id": "2011", "name": "乐器", "group": "分类"},
        {"id": "68", "name": "经典", "group": "热门"},
        {"id": "12", "name": "国风", "group": "热门"},
    ],
    "kw": [
        {"id": "211", "name": "经典老歌", "group": "专区"},
        {"id": "87", "name": "网红", "group": "专区"},
        {"id": "1", "name": "DJ", "group": "专区"},
        {"id": "13", "name": "轻音乐", "group": "专区"},
        {"id": "12", "name": "国风", "group": "专区"},
        {"id": "156", "name": "影视", "group": "专区"},
        {"id": "6", "name": "动漫", "group": "专区"},
        {"id": "17", "name": "新歌首发", "group": "专区"},
        {"id": "2200", "name": "情歌", "group": "热门标签"},
        {"id": "146", "name": "伤感", "group": "热门标签"},
        {"id": "155", "name": "怀旧", "group": "热门标签"},
        {"id": "127", "name": "古风", "group": "热门标签"},
        {"id": "621", "name": "网络", "group": "热门标签"},
        {"id": "173", "name": "轻音乐", "group": "热门标签"},
    ],
    # wy 的 cat 参数只认中文名（catlist 已下线，无法动态拉取）→ 固化常用分类
    "wy": [
        {"id": "华语", "name": "华语", "group": "语种"},
        {"id": "欧美", "name": "欧美", "group": "语种"},
        {"id": "日语", "name": "日语", "group": "语种"},
        {"id": "韩语", "name": "韩语", "group": "语种"},
        {"id": "粤语", "name": "粤语", "group": "语种"},
        {"id": "流行", "name": "流行", "group": "风格"},
        {"id": "摇滚", "name": "摇滚", "group": "风格"},
        {"id": "民谣", "name": "民谣", "group": "风格"},
        {"id": "电子", "name": "电子", "group": "风格"},
        {"id": "说唱", "name": "说唱", "group": "风格"},
        {"id": "古风", "name": "古风", "group": "风格"},
        {"id": "轻音乐", "name": "轻音乐", "group": "风格"},
        {"id": "ACG", "name": "ACG", "group": "风格"},
        {"id": "影视原声", "name": "影视原声", "group": "主题"},
        {"id": "怀旧", "name": "怀旧", "group": "主题"},
        {"id": "治愈", "name": "治愈", "group": "主题"},
        {"id": "伤感", "name": "伤感", "group": "心情"},
        {"id": "安静", "name": "安静", "group": "心情"},
        {"id": "运动", "name": "运动", "group": "场景"},
        {"id": "学习", "name": "学习", "group": "场景"},
        {"id": "睡前", "name": "睡前", "group": "场景"},
    ],
    "mg": [],
}

# 支持分类筛选的平台（咪咕无分类体系）
CATEGORY_PLATFORMS = ("tx", "wy", "kw", "kg")

_UA_PC = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
_UA_MOBILE = "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) AppleWebKit/605.1.15"

_TX_HEADERS = {"Referer": "https://y.qq.com/", "User-Agent": _UA_PC}
_WY_HEADERS = {"Referer": "https://music.163.com", "User-Agent": _UA_PC}
_KW_HEADERS = {"Referer": "https://www.kuwo.cn/", "User-Agent": _UA_PC}
_KG_HEADERS = {"Referer": "https://www.kugou.com/", "User-Agent": _UA_PC}
_MG_HEADERS = {"Referer": "https://m.music.migu.cn/", "User-Agent": _UA_MOBILE}

_TAG_RE = re.compile(r"<[^>]+>")


def clean_html(value: Any) -> str:
    """剥离上游文本里的 HTML 标签与实体（歌单简介/标题常见）。"""
    text = _TAG_RE.sub("", str(value or ""))
    return html.unescape(text).strip()


def format_play_count(value: Any) -> str:
    """播放量 → 1.2万 / 3.4亿 之类的短文本（与柠檬 formatPlayCount 语义一致）。"""
    try:
        n = int(value or 0)
    except (TypeError, ValueError):
        return ""
    if n <= 0:
        return ""
    if n >= 100_000_000:
        return f"{n / 100_000_000:.1f}亿"
    if n >= 10_000:
        return f"{n / 10_000:.1f}万"
    return str(n)


def _seconds(value: Any) -> float:
    """时长归一：兼容毫秒（>10000 视为 ms）与秒。"""
    try:
        n = float(value or 0)
    except (TypeError, ValueError):
        return 0.0
    if n <= 0:
        return 0.0
    return n / 1000.0 if n > 10000 else n


async def _get(client: httpx.AsyncClient, url: str, headers: dict, *, timeout: float = 15.0):
    """GET 并返回 JSON（dict/list）；失败返回 None，不抛。"""
    try:
        resp = await client.get(url, headers=headers, timeout=timeout)
        if resp.status_code != 200:
            logger.debug("platform playlist %s http %s", url[:80], resp.status_code)
            return None
        return resp.json()
    except Exception as exc:  # noqa: BLE001
        logger.debug("platform playlist %s failed: %s", url[:80], exc)
        return None


async def _post_json(client: httpx.AsyncClient, url: str, payload: dict, headers: dict, *, timeout: float = 15.0):
    try:
        resp = await client.post(url, json=payload, headers=headers, timeout=timeout)
        if resp.status_code != 200:
            logger.debug("platform playlist POST %s http %s", url[:80], resp.status_code)
            return None
        return resp.json()
    except Exception as exc:  # noqa: BLE001
        logger.debug("platform playlist POST %s failed: %s", url[:80], exc)
        return None


def _card(source: str, pid: str, name: str, author: str = "", img: str = "",
          play_count: str = "", total: int = 0, desc: str = "") -> dict:
    """歌单卡（广场列表项）：字段与柠檬 mapRecommendItem 对齐。"""
    return {
        "id": str(pid),
        "name": clean_html(name),
        "author": clean_html(author),
        "img": str(img or ""),
        "play_count": play_count,
        "total": int(total or 0),
        "desc": clean_html(desc),
        "source": source,
    }


def _song(source: str, sid: str, title: str, artist: str = "", album: str = "",
          duration_s: float = 0.0, ext: str = "mp3", cover_url: str = "") -> dict:
    """歌单曲目 → 在线条目统一形状（app.build_online_track 的输入）。

    id 必须是 **洛雪形态** `lx:<platform>:<identifier>`：播放链最终由用户
    自建洛雪源解析（`online:lx:<platform>:<id>` → lxmusic-service
    `/api/v1/track/url`），identifier 契约见 lxserver_client.extract_track_identifier
    （kg 用 hash、mg 用 copyrightId、其余用 songmid/rid）。
    绝不能写成 `musicdl` 的 `<platform>:<id>` 短路形态——那会被
    `_source_enabled()` 当成 musicdl 平台名去比对用户的 MUSICDL_SOURCES
    白名单，绝大多数情况下直接被拒播。
    """
    if not sid:
        return {}
    return {
        "id": f"lx:{source}:{sid}",
        "source": "lx",
        "lx_source": source,
        "title": clean_html(title),
        "artist": clean_html(artist),
        "album": clean_html(album),
        "duration_s": float(duration_s or 0),
        "ext": str(ext or "mp3"),
        "cover_url": str(cover_url or ""),
    }


# --------------------------------------------------------------- 推荐歌单广场 --

async def _tx_recommend(client: httpx.AsyncClient, sort: str, page: int, limit: int,
                        category: str = "") -> list[dict]:
    """QQ 歌单广场。

    统一走 `get_playlist_by_tag`：它的 `id` 参数**就是**分类 id（`10000000` = 全部），
    传 `id=<分类id>` 即按该分类过滤。实测 total 随分类变化（国语 4812、流行 5325、
    民谣 830…），内容与分类名一致。

    注意（v2.8.3 修正）：早前误用 `get_playlist_by_category` —— 该 method 的 `id`/
    `titleid` **不参与分类过滤**，恒返回一个与分类无关的固定小集合（total=9，多为
    「音乐节」类歌单），导致「选国语却出日本音乐节」。**勿再改回该 method。**
    """
    order = 2 if sort == "new" else 5
    cat = str(category or "").strip()
    tag_id = int(cat) if (cat.isdigit() and cat != "10000000") else 10000000
    method = "get_playlist_by_tag"
    param = {"id": tag_id, "sin": limit * (page - 1), "size": limit,
             "order": order, "cur_page": page}
    payload = {
        "comm": {"cv": 1602, "ct": 20},
        "playlist": {"method": method, "param": param,
                     "module": "playlist.PlayListPlazaServer"},
    }
    url = ("https://u.y.qq.com/cgi-bin/musicu.fcg?format=json&data="
           + quote(json.dumps(payload, ensure_ascii=False), safe=""))
    data = await _get(client, url, _TX_HEADERS)
    pl = (data or {}).get("playlist", {}).get("data") if isinstance(data, dict) else None
    if not isinstance(pl, dict):
        return []
    out = []
    for item in pl.get("v_playlist") or []:
        if not isinstance(item, dict):
            continue
        out.append(_card(
            "tx",
            item.get("tid") or "",
            item.get("title") or "",
            (item.get("creator_info") or {}).get("nick") or "",
            item.get("cover_url_medium") or item.get("cover_url_big") or "",
            format_play_count(item.get("access_num")),
            item.get("song_num") or item.get("songnum") or item.get("total_song_num") or 0,
            item.get("desc") or "",
        ))
    return out


async def _wy_recommend(client: httpx.AsyncClient, sort: str, page: int, limit: int,
                        category: str = "") -> list[dict]:
    order = "new" if sort == "new" else "hot"
    # cat 参数只认中文分类名（catlist 列表接口已 404，分类名固化在 CATEGORY_GROUP_FALLBACK）
    cat = str(category or "").strip() or "全部"
    url = ("https://music.163.com/api/playlist/list?cat=" + quote(cat)
           + f"&order={order}&limit={limit}&offset={(page - 1) * limit}")
    data = await _get(client, url, _WY_HEADERS)
    if not isinstance(data, dict) or not data.get("playlists"):
        return []
    out = []
    for item in data["playlists"]:
        if not isinstance(item, dict):
            continue
        out.append(_card(
            "wy",
            item.get("id") or "",
            item.get("name") or "",
            (item.get("creator") or {}).get("nickname") or "",
            item.get("coverImgUrl") or "",
            format_play_count(item.get("playCount")),
            item.get("trackCount") or 0,
            item.get("description") or "",
        ))
    return out


async def _kw_recommend(client: httpx.AsyncClient, sort: str, page: int, limit: int,
                        category: str = "") -> list[dict]:
    order = "new" if sort == "new" else "hot"
    cat = str(category or "").strip()
    if cat.isdigit():
        # 指定分类：getTagPlayList（实测 id 即分类 id，digest 传 10000 或 43 均可）
        url = ("http://wapi.kuwo.cn/api/pc/classify/playlist/getTagPlayList"
               f"?loginUid=0&loginSid=0&pn={page}&rn={limit}&id={cat}&digest=10000")
    else:
        url = ("http://wapi.kuwo.cn/api/pc/classify/playlist/getRcmPlayList"
               f"?loginUid=0&loginSid=0&appUid=76039576&pn={page}&rn={limit}&order={order}")
    data = await _get(client, url, _KW_HEADERS)
    inner = (data or {}).get("data", {}).get("data") if isinstance(data, dict) else None
    if not isinstance(inner, list):
        return []
    out = []
    for item in inner:
        if not isinstance(item, dict):
            continue
        # 保留 digest 前缀：kw 歌单详情按 digest 分派解析（与柠檬一致）
        pid = f"digest-{item.get('digest')}__{item.get('id')}"
        out.append(_card(
            "kw", pid, item.get("name") or "", item.get("uname") or "",
            item.get("img") or "", format_play_count(item.get("listencnt")),
            item.get("total") or 0, item.get("desc") or "",
        ))
    return out


async def _kg_resolve_tag(client: httpx.AsyncClient, category: str) -> str:
    """酷狗分类 id → 取歌单接口要的 special_tag_id（tag/info 解析；失败原样返回）。"""
    cat = str(category or "").strip()
    if not cat.isdigit():
        return ""
    url = f"http://mobilecdnbj.kugou.com/api/v3/tag/info?&id={cat}&apiver=2"
    data = await _get(client, url, _KG_HEADERS)
    if isinstance(data, dict):
        info = data.get("data")
        if isinstance(info, dict):
            stid = info.get("special_tag_id")
            if stid:
                return str(stid)
    return cat


async def _kg_recommend(client: httpx.AsyncClient, sort: str, page: int, limit: int,
                        category: str = "") -> list[dict]:
    sort_map = {"hot": "6", "new": "7", "recommend": "5"}
    t = sort_map.get(sort, sort_map["recommend"])
    cat = str(category or "").strip()
    if cat.isdigit():
        # 指定分类：tag/specialList（tagid 需先由 tag/info 解析）
        tagid = await _kg_resolve_tag(client, cat) or cat
        url = ("http://mobilecdnbj.kugou.com/api/v3/tag/specialList"
               f"?plat=0&page={page}&tagid={tagid}&pagesize={limit}&ugc=1&id={cat}&sort=2")
        data = await _get(client, url, _KG_HEADERS)
        rows = (data or {}).get("data", {}).get("info") if isinstance(data, dict) else None
        if not isinstance(rows, list):
            rows = (data or {}).get("data", {}).get("list") if isinstance(data, dict) else None
        if not isinstance(rows, list):
            # 分类接口不可用时退回通用热门（保证不空）
            return await _kg_recommend(client, sort, page, limit, "")
    else:
        url = ("http://www2.kugou.kugou.com/yueku/v9/special/getSpecial"
               f"?is_ajax=1&cdn=cdn&t={t}&c=&p={page}")
        data = await _get(client, url, _KG_HEADERS)
        rows = (data or {}).get("special_db") if isinstance(data, dict) else None
        if not isinstance(rows, list):
            return []
    out = []
    for item in rows:
        if not isinstance(item, dict):
            continue
        img = str(item.get("imgurl") or item.get("img") or "").replace("{size}", "400")
        # 分类接口用 specialid，通用接口用 specialid；两者字段名一致
        sid = item.get("specialid") or item.get("id") or ""
        out.append(_card(
            "kg", f"id_{sid}", item.get("specialname") or item.get("name") or "",
            item.get("nickname") or item.get("singername") or item.get("username") or "",
            img, format_play_count(item.get("play_count") or item.get("total_play_count")
                                   or item.get("playcount")),
            item.get("songcount") or item.get("song_count") or 0,
            item.get("intro") or item.get("description") or "",
        ))
    return out


def _extract_mg_items(contents: list, out: list, seen: set) -> None:
    """递归抽取咪咕歌单广场条目。

    实测（2026-10 复验）：广场里歌单卡是 `resType=2021` + `resId=<playlistId>`
    的条目（`viewId` 形如 `4025-<resId>`），`resId` 就是歌单详情接口要的
    playlistId。`4006-<columnId>` 是「自定义位图标题」运营位（如 "日落后，音乐起"），
    不是歌单，拉详情必得"歌单不存在"——必须排除。
    优先取 `resId`（最准确），回落 viewId 的 4025- 尾段。
    """
    for item in contents or []:
        if not isinstance(item, dict):
            continue
        sub = item.get("contents")
        if isinstance(sub, list) and sub:
            _extract_mg_items(sub, out, seen)
        pid = ""
        res_type = str(item.get("resType") or "")
        res_id = str(item.get("resId") or "")
        if res_type == "2021" and res_id:
            pid = res_id
        elif res_id:
            # 复位型 data：只认带 song-list action 的条目
            if "song-list-info" in str(item.get("action") or ""):
                pid = res_id
        if not pid:
            view = str(item.get("viewId") or "")
            if view.startswith("4025-"):
                pid = view.split("-", 1)[1]
        if not pid:
            continue
        name = item.get("txt") or item.get("txt4")
        if name and pid not in seen:
            seen.add(pid)
            img = str(item.get("img") or item.get("img2") or "")
            if img.startswith("/"):
                img = "https://d.musicapp.migu.cn" + img
            out.append(_card("mg", pid, name, "", img, "", 0, ""))


async def _mg_recommend(client: httpx.AsyncClient, sort: str, page: int, limit: int,
                        category: str = "") -> list[dict]:
    # 咪咕无分类体系（只有运营楼层），category 参数忽略
    url = ("https://app.c.nf.migu.cn/pc/bmw/page-data/playlist-square-recommend/v1.0"
           f"?templateVersion=2&pageNo={page}")
    data = await _get(client, url, _MG_HEADERS)
    contents = (data or {}).get("data", {}).get("contents") if isinstance(data, dict) else None
    if not isinstance(contents, list):
        return []
    out: list[dict] = []
    _extract_mg_items(contents, out, set())
    # 咪咕首页是"运营楼层"结构，单次可抽出上百条；按 limit 截断，避免歌单列表被刷屏
    return out[:max(1, int(limit or 30))]


_RECOMMEND_MAP = {
    "tx": _tx_recommend,
    "wy": _wy_recommend,
    "kw": _kw_recommend,
    "kg": _kg_recommend,
    "mg": _mg_recommend,
}


async def fetch_platform_playlists(
    client: httpx.AsyncClient, source: str, sort: str = "hot", page: int = 1, limit: int = 30,
    category: str = "",
) -> list[dict]:
    """某平台的推荐歌单卡列表；不支持/失败返回 []。

    category 为空 = 该平台「全部」（与原行为一致）。指定分类时按平台语义取值：
    tx/kg/kw 传分类数字 id，wy 传中文分类名。mg 无分类体系，忽略该参数。

    部分上游（酷狗/咪咕）忽略请求的 size 参数按自己的页长返回，这里统一截断
    到 limit 内——否则注入侧 `recommend_playlist_page_size` 形同虚设。
    """
    src = str(source or "").strip()
    fn = _RECOMMEND_MAP.get(src)
    if not fn:
        return []
    limit = max(1, int(limit or 30))
    cat = str(category or "").strip()
    try:
        if cat and src in CATEGORY_PLATFORMS:
            cards = await fn(client, str(sort or "hot"), max(1, int(page or 1)), limit, cat)
        else:
            cards = await fn(client, str(sort or "hot"), max(1, int(page or 1)), limit)
    except Exception as exc:  # noqa: BLE001
        logger.debug("fetch_platform_playlists %s failed: %s", source, exc)
        return []
    return cards[:limit] if isinstance(cards, list) else []


async def fetch_platform_categories(
    client: httpx.AsyncClient, source: str,
) -> list[dict[str, str]]:
    """某平台的歌单分类列表 [{id, name, group}]；不支持/失败返回内置兜底表。

    tx 走官方分类树接口（权威、动态）；kg/kw 走各自的 tag 接口；
    wy 的 catlist 已下线（404），直接用固化表；mg 无分类体系返回 []。
    一律不抛异常——UI 侧拿到空列表即隐藏该平台的分类选择。
    """
    src = str(source or "").strip()
    fallback = list(CATEGORY_GROUP_FALLBACK.get(src) or [])
    if src not in CATEGORY_PLATFORMS:
        return []
    try:
        if src == "tx":
            rows = await _tx_categories(client)
        elif src == "kg":
            rows = await _kg_categories(client)
        elif src == "kw":
            rows = await _kw_categories(client)
        else:
            rows = []
        if rows:
            return rows
    except Exception as exc:  # noqa: BLE001
        logger.debug("fetch_platform_categories %s failed: %s", source, exc)
    return fallback


async def _tx_categories(client: httpx.AsyncClient) -> list[dict[str, str]]:
    """QQ 官方分类树（fcg_get_diss_tag_conf），返回 6 组 60+ 分类。"""
    url = ("https://c.y.qq.com/splcloud/fcgi-bin/fcg_get_diss_tag_conf.fcg"
           "?picmid=1&rnd=0.1&g_tk=5381&format=json&inCharset=utf8&outCharset=utf-8")
    data = await _get(client, url, _TX_HEADERS)
    groups = (data or {}).get("data", {}).get("categories") if isinstance(data, dict) else None
    if not isinstance(groups, list):
        return []
    out: list[dict[str, str]] = []
    for g in groups:
        if not isinstance(g, dict):
            continue
        gname = clean_html(g.get("categoryGroupName"))
        for it in g.get("items") or []:
            if not isinstance(it, dict):
                continue
            cid = it.get("categoryId")
            cname = clean_html(it.get("categoryName"))
            # 跳过「全部」（10000000）——它就是「不筛选」，由空 category 表达
            if not cid or not cname or str(cid) == "10000000":
                continue
            out.append({"id": str(cid), "name": cname, "group": gname})
    return out


async def _kg_categories(client: httpx.AsyncClient) -> list[dict[str, str]]:
    """酷狗分类树（tag/list pid=0）。children 为空，只取顶层父分类。"""
    url = "http://mobilecdnbj.kugou.com/api/v3/tag/list?pid=0&apiver=2&plat=0"
    data = await _get(client, url, _KG_HEADERS)
    rows = (data or {}).get("data", {}).get("info") if isinstance(data, dict) else None
    if not isinstance(rows, list):
        return []
    out: list[dict[str, str]] = []
    for it in rows:
        if not isinstance(it, dict):
            continue
        cid = it.get("id")
        cname = clean_html(it.get("name"))
        if not cid or not cname:
            continue
        out.append({"id": str(cid), "name": cname, "group": "分类"})
    return out


async def _kw_categories(client: httpx.AsyncClient) -> list[dict[str, str]]:
    """酷我分类（getTagList：data[].data[]，每项含 id/name/digest）。"""
    url = ("http://wapi.kuwo.cn/api/pc/classify/playlist/getTagList"
           "?loginUid=0&loginSid=0")
    data = await _get(client, url, _KW_HEADERS)
    groups = (data or {}).get("data") if isinstance(data, dict) else None
    if not isinstance(groups, list):
        return []
    out: list[dict[str, str]] = []
    for g in groups:
        if not isinstance(g, dict):
            continue
        gname = clean_html(g.get("name")) or "专区"
        for it in g.get("data") or []:
            if not isinstance(it, dict):
                continue
            cid = it.get("id")
            cname = clean_html(it.get("name"))
            if not cid or not cname:
                continue
            out.append({"id": str(cid), "name": cname, "group": gname})
    return out


# --------------------------------------------------------------- 歌单详情曲目 --

def _map_tx_song(item: dict) -> dict:
    album_mid = str((item.get("album") or {}).get("mid") or item.get("albummid") or "")
    song_mid = str(item.get("mid") or item.get("songmid") or "")
    name = item.get("name") or item.get("title") or item.get("songname") or ""
    singer = ""
    if isinstance(item.get("singer"), list):
        singer = "/".join(str(s.get("name") or "") for s in item["singer"] if isinstance(s, dict))
    else:
        singer = str(item.get("singername") or "")
    cover = (f"https://y.gtimg.cn/music/photo_new/T002R500x500M000{album_mid}.jpg"
             if album_mid and album_mid != "0" else "")
    return _song("tx", song_mid, str(name), singer,
                 str((item.get("album") or {}).get("name") or item.get("albumname") or ""),
                 _seconds(item.get("interval")), "mp3", cover)


def _map_wy_song(item: dict) -> dict:
    pic = str((item.get("al") or {}).get("picUrl") or (item.get("album") or {}).get("picUrl") or "")
    artists = item.get("ar") or item.get("artists") or []
    singer = "/".join(str(a.get("name") or "") for a in artists if isinstance(a, dict))
    return _song("wy", str(item.get("id") or ""), str(item.get("name") or ""), singer,
                 str((item.get("al") or {}).get("name") or (item.get("album") or {}).get("name") or ""),
                 _seconds(item.get("dt") or item.get("duration")), "mp3", pic)


def _map_kw_song(item: dict) -> dict:
    rid = str(item.get("rid") or item.get("id") or "").replace("MUSIC_", "") or str(item.get("musicrid") or "").replace("MUSIC_", "")
    pic = str(item.get("pic") or item.get("albumpic") or "").replace("/120/", "/500/")
    return _song("kw", rid, str(item.get("name") or item.get("songname") or ""),
                 str(item.get("artist") or item.get("singer") or ""),
                 str(item.get("album") or item.get("albumname") or ""),
                 _seconds(item.get("duration") or item.get("song_duration")), "mp3", pic)


def _map_kg_song(item: dict) -> dict:
    """酷狗曲目映射（字段覆盖对齐柠檬 mapKgSongItem + mobilecdn 实测形状）。

    mobilecdn `special/song` 的实测字段：`filename` = "歌手 - 歌名"、
    `remark` = 歌名、封面在 `trans_param.union_cover`（带 {size} 占位）。
    独立歌手字段（author_name/singerinfo/authors）在部分接口形态下存在，
    优先使用；都没有再从 filename 拆。
    """
    audio = item.get("audio_info") or item
    hash_ = str(audio.get("hash") or item.get("hash") or item.get("FileHash") or "")
    name = str(item.get("songname") or item.get("SongName") or item.get("name")
               or item.get("audio_name") or item.get("remark") or "")
    singer = str(item.get("author_name") or item.get("SingerName") or item.get("singer") or "")
    if not singer and isinstance(item.get("authors"), list):
        singer = "、".join(str(a.get("author_name") or "") for a in item["authors"]
                          if isinstance(a, dict) and a.get("author_name"))
    if not singer and isinstance(item.get("singerinfo"), list):
        singer = "、".join(str(a.get("name") or a.get("author_name") or a.get("singername") or "")
                          for a in item["singerinfo"] if isinstance(a, dict))
    # filename 常见 "歌手 - 歌名"：补歌手，并在歌名为空时补歌名
    fname = str(item.get("filename") or "")
    if " - " in fname:
        head, _sep, tail = fname.partition(" - ")
        if head.strip() and tail.strip():
            if not singer:
                singer = head.strip()
            if not name:
                name = tail.strip()
    album = ""
    for key in ("album_info", "albuminfo"):
        obj = item.get(key)
        if isinstance(obj, dict):
            album = str(obj.get("album_name") or obj.get("name") or "")
            if album:
                break
    album = album or str(item.get("AlbumName") or item.get("album") or item.get("album_name") or "")
    cover = str(item.get("Image") or item.get("AlbumImage") or item.get("album_img")
                or item.get("imgurl") or audio.get("imgurl") or item.get("cover")
                or item.get("img") or item.get("pic") or "")
    if not cover and isinstance(item.get("album_info"), dict):
        cover = str(item["album_info"].get("sizable_cover") or item["album_info"].get("imgurl") or "")
    if not cover:
        tp = item.get("trans_param")
        if isinstance(tp, dict):
            cover = str(tp.get("union_cover") or tp.get("cover") or "")
    if not cover:
        cover = str(item.get("album_sizable_cover") or "")
    cover = cover.replace("{size}", "400")
    dur = audio.get("timelength") or item.get("timelen") or item.get("Duration") or item.get("duration") or 0
    return _song("kg", hash_, name, singer, album, _seconds(dur), "mp3", cover)


def _map_mg_song(item: dict) -> dict:
    """咪咕曲目映射。

    `albumImg` 常为 null，封面在 `img1`/`img2`（可能是 `/data/oss/...` 相对路径，
    需补 https://d.musicapp.migu.cn 前缀）。ID 优先 copyrightId（lx 契约要求），
    回落 songId。
    """
    o = item.get("objectInfo") or item
    singers = o.get("singerList") or o.get("singers") or []
    singer = "/".join(str(s.get("name") or "") for s in singers if isinstance(s, dict)) or str(o.get("singer") or "")
    pics = [o.get("albumImg"), o.get("img1"), o.get("img2"), o.get("img3")]
    pic = ""
    for p in pics:
        p = str(p or "")
        if p:
            pic = p
            break
    if pic.startswith("/"):
        pic = "https://d.musicapp.migu.cn" + pic
    return _song("mg", str(o.get("copyrightId") or o.get("songId") or ""),
                 str(o.get("songName") or o.get("name") or ""), singer,
                 str(o.get("album") or ""), _seconds(o.get("duration")), "mp3", pic)


async def _tx_tracks(client: httpx.AsyncClient, pid: str, limit: int) -> list[dict]:
    url = ("https://c.y.qq.com/qzone/fcg-bin/fcg_ucc_getcdinfo_byids_cp.fcg"
           f"?type=1&json=1&utf8=1&onlysong=0&new_format=1&disstid={pid}"
           f"&song_begin=0&song_num={limit}"
           "&loginUin=0&hostUin=0&format=json&inCharset=utf8&outCharset=utf-8"
           "&notice=0&platform=yqq.json&needNewCode=0")
    data = await _get(client, url, {**_TX_HEADERS, "Referer": f"https://y.qq.com/n/yqq/playlist/{pid}.html"})
    cd = (data or {}).get("cdlist", [{}])[0] if isinstance(data, dict) else None
    if not isinstance(cd, dict):
        return []
    return [t for t in (_map_tx_song(s) for s in cd.get("songlist") or [] if isinstance(s, dict)) if t]


async def _wy_tracks(client: httpx.AsyncClient, pid: str, limit: int) -> list[dict]:
    url = f"https://music.163.com/api/v6/playlist/detail?id={pid}&n={limit}&s=8"
    data = await _get(client, url, _WY_HEADERS)
    pl = (data or {}).get("playlist") if isinstance(data, dict) else None
    if not isinstance(pl, dict):
        return []
    return [t for t in (_map_wy_song(s) for s in (pl.get("tracks") or []) if isinstance(s, dict)) if t]


async def _kw_tracks(client: httpx.AsyncClient, pid: str, limit: int) -> list[dict]:
    # 广场卡的 id 形如 digest-<digest>__<id>；digest 含义与柠檬一致：
    # 8/13 为常规歌单直接用后段 id，5 为榜单分类需先查 sourceid。
    real_id = pid
    m = re.match(r"^digest-(\d+)__(\d+)$", str(pid))
    if m:
        digest, tail = m.group(1), m.group(2)
        if digest == "5":
            info = await _get(
                client,
                f"http://qukudata.kuwo.cn/q.k?op=query&cont=ninfo&node={tail}&pn=0&rn=1&fmt=json&src=mbox&level=2",
                _KW_HEADERS,
            )
            child = (info or {}).get("child") or []
            real_id = str((child[0] or {}).get("sourceid") or tail) if child else tail
        else:
            real_id = tail
    url = ("http://nplserver.kuwo.cn/pl.svc?op=getlistinfo"
           f"&pid={real_id}&pn=0&rn={limit}&encode=utf8&keyset=pl2012"
           "&identity=kuwo&pcmp4=1&vipver=MUSIC_9.0.5.0_W1&newver=1")
    raw = None
    try:
        resp = await client.get(url, headers=_KW_HEADERS, timeout=15.0)
        if resp.status_code == 200:
            text = resp.text.strip()
            try:
                raw = resp.json()
            except Exception:
                # 酷我该端点历史上有非标准 JSON（单引号）返回
                raw = json.loads(text.replace("'", '"'))
    except Exception as exc:  # noqa: BLE001
        logger.debug("kw playlist tracks failed: %s", exc)
    if not isinstance(raw, dict):
        return []
    return [t for t in (_map_kw_song(s) for s in (raw.get("musiclist") or []) if isinstance(s, dict)) if t]


async def _kg_tracks(client: httpx.AsyncClient, pid: str, limit: int) -> list[dict]:
    special_id = str(pid).replace("id_", "")
    url = ("http://mobilecdn.kugou.com/api/v3/special/song"
           f"?specialid={special_id}&page=1&pagesize={limit}&version=9108")
    data = await _get(client, url, _KG_HEADERS)
    rows = (data or {}).get("data", {}).get("info") if isinstance(data, dict) else None
    if not isinstance(rows, list):
        return []
    return [t for t in (_map_kg_song(s) for s in rows if isinstance(s, dict)) if t]


async def _mg_tracks(client: httpx.AsyncClient, pid: str, limit: int) -> list[dict]:
    page = 1
    out: list[dict] = []
    while len(out) < limit and page <= 5:
        url = ("https://app.c.nf.migu.cn/MIGUM3.0/resource/playlist/song/v2.0"
               f"?pageNo={page}&pageSize=50&playlistId={pid}")
        data = await _get(client, url, _MG_HEADERS)
        rows = (data or {}).get("data", {}).get("songList") if isinstance(data, dict) else None
        if not isinstance(rows, list) or not rows:
            break
        out.extend(t for t in (_map_mg_song(s) for s in rows if isinstance(s, dict)) if t)
        page += 1
    return out[:limit]


_TRACKS_MAP = {
    "tx": _tx_tracks,
    "wy": _wy_tracks,
    "kw": _kw_tracks,
    "kg": _kg_tracks,
    "mg": _mg_tracks,
}


async def fetch_playlist_tracks(
    client: httpx.AsyncClient, source: str, playlist_id: str, limit: int = 100,
) -> list[dict]:
    """单个平台歌单的曲目（在线条目统一形状）；失败返回 []。"""
    fn = _TRACKS_MAP.get(str(source or "").strip())
    if not fn or not playlist_id:
        return []
    try:
        return await fn(client, str(playlist_id), max(1, min(int(limit or 100), 500)))
    except Exception as exc:  # noqa: BLE001
        logger.debug("fetch_playlist_tracks %s/%s failed: %s", source, playlist_id, exc)
        return []


def platform_label(source: str) -> str:
    return PLATFORM_LABELS.get(str(source or "").strip(), str(source or ""))
