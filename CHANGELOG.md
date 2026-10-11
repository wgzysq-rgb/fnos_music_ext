# 更新日志 (Changelog)

本项目所有显著变更均记录于此文件。
格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，版本号遵循语义化版本。

## [Unreleased]

## [2.8.14] - 2026-10-11

### 修复

- **关掉「平台推荐歌单」总开关后，歌单列表里仍有平台推荐歌单（2.8.12 引入的回归）**。
  - 根因：`_load_discover_playlists` 只看了「平台白名单 `_discover_sources()`」是否非空，
    **没看总开关 `_discover_enabled()`**。2.8.12 起 `discover_on` 改成「平台歌单 **或** 排行榜
    任一开启」，于是用户只开了排行榜时，平台白名单/分类即便残留也会被连带注入。
  - 修复：平台卡必须同时满足「总开关开 + 白名单非空」；榜单卡同样必须满足「排行榜总开关开
    + 勾选清单非空」。两个总开关各自独立生效，互不牵连。
  - 回归测试：`test_discover_playlists.py::test_playlist_cards_gated_by_master_switch_even_with_charts_on`、
    `::test_chart_cards_gated_by_charts_master_switch`。

## [2.8.13] - 2026-10-11

### 修复

- **设置页保存排行榜配置报「不支持的配置键: FNMUSIC_RECOMMEND_CHARTS」**。
  - 根因：2.8.12 新增的榜单配置键**没有登记进 WebUI 的 `SCHEMA` 白名单**（`/api/config`
    只接受白名单内的键），保存直接被拒。
  - 修复：`SCHEMA` 补 `FNMUSIC_RECOMMEND_CHARTS` / `FNMUSIC_RECOMMEND_CHARTS_LIST`，
    并补进 `.env.example`（有回归测试 `test_recommend_playlist_keys_documented_in_env_example` 兜）。

### 变更

- **排行榜改为「总开关 + 勾选清单」**（与平台推荐歌单同款交互）：
  - 新总开关 `FNMUSIC_RECOMMEND_CHARTS`（bool，默认关，对应设置页「平台排行榜」）；
    勾选清单改到 `FNMUSIC_RECOMMEND_CHARTS_LIST`（csv `<平台>:<榜单id>`，空 = 不注入）。
  - 设置页「推荐」下新增独立开关，关闭时隐藏「选择榜单」细节区。
- **排行榜新增酷狗（kg）/ 酷我（kw）**：这两家的榜单曲目走 **lxserver 统一榜单通道**
  （`lxmusic-service` 新增 `/api/v1/charts/boards` + `/api/v1/charts/list` 两个端点透传
  lxserver 的 `leaderboard/boards|list`），因其平台自有 Web 接口已不可靠。QQ（tx）走
  `musicu.fcg`、网易（wy）走 `/api/toplist`（均平台直连）。咪咕无榜单体系。
  - 回归测试：`test_discover_playlists.py::test_lx_charts_kg_via_wrapper`、
    `test_client_and_app.py::test_charts_boards_endpoint`/`test_charts_list_endpoint`。

## [2.8.12] - 2026-10-11

### 新增

- **推荐歌单新增「排行榜」注入（QQ音乐 / 网易云，勾选式）**。
  - 设置页「推荐」下新增「排行榜（榜单）」区：按平台列出该平台**全部榜单**，勾选即注入、
    取消即移除（写入新配置 `FNMUSIC_RECOMMEND_CHARTS`，形如 `tx:26,tx:4,wy:19723756`）。
  - 选中的榜单在「推荐歌单（发现）」里各显示为一张卡片，点进去播放该榜曲目；与「平台推荐歌单」
    **互为独立开关**（只勾榜单、不勾平台歌单也照常注入），空 = 不注入任何榜单。
  - 数据源：QQ `musicu.fcg`（`ToplistInfoServer/GetAll` 列表 + `GetDetail` 曲目，实测 29 个榜：
    巅峰/地区/特色/全球榜）；网易 `/api/toplist`（63 个榜）+ 复用现有网易歌单取曲目。
    榜单列表拉不到时回落内置兜底表，设置页不空。
  - 新增 `proxy/platform_charts.py`；`webui-service` 新增 `/api/playlist-charts` 端点；
    `platform_charts.py` 已同步加入 `container/Dockerfile` 的 `/srv/proxy/` COPY 清单与
    `.dockerignore` 白名单（否则容器内 `from proxy import platform_charts` 会 ImportError）。
  - 回归测试：proxy `test_discover_playlists.py::test_discover_chart_entries_parse` /
    `::test_load_discover_charts_builds_cards` / `::test_load_discover_tracks_routes_chart_prefix`
    等；webui `test_webui.py::test_playlist_charts_*`。

## [2.8.11] - 2026-10-11

### 修复

- **「边听边存/收藏自动绑定」对部分在线曲目永远存不下（`.part` 一闪即逝）——真因是无损档内容损坏 + lx 无 mp3 兜底**。
  - 现象（用户实测 + 日志实锤）：推荐歌单里播放时保存目录出现 `.part` 很快消失、歌曲没落盘。
    日志：`tee finalize rejected corrupt lossless for online:lx:tx:<hash>` /
    `audio decode check failed for ... (ext=flac bytes=...)` / `Background full fetch failed ... RuntimeError`。
  - 根因：**某些平台（实测 tx/QQ，及部分 kg）的无损直链能解析、能下满，但内容损坏**——
    在 NAS 上用 curl 下满该文件后 `ffmpeg -v error` 报 `invalid sync code / invalid frame header /
    decode_frame() failed`（`ffprobe` 头正常、`ffmpeg` 退出码 0 但解码报错）。代理的落盘前
    解码校验（`_audio_file_ok`：要求"退出码 0 且无任何 stderr"）因此**拒绝该文件并删除**
    → `.part` 消失、歌曲存不下。而同曲目的**标准音质（mp3）档是完好的**（`ID3…`、解码零报错），
    wy（网易）的无损档也是完好的。
  - 修复：**无损档被判损坏后，lx 通道必须真正降档到有损档重取**（此前 `force_mp3` 只对 musicdl
    分支生效、lx 分支被忽略 → 重试仍取无损 → 再次失败 → 永远存不下）：
    - `resolve_lx_url(..., avoid_lossless=True)`：跳过 `lossless`，改取 `standard → high`。
    - `_open_online_stream` / `_transcode_source` 的 lx 分支在「强制 mp3 或该曲无损已拉黑」时传
      `avoid_lossless=True`。
    - 顺带给 `_audio_file_ok` 补上失败原因日志（`rc=`+ffmpeg stderr 首行），下次不必再猜。
  - 回归测试：`test_transcode_compat.py::test_resolve_lx_url_avoid_lossless_skips_lossless_tier`。

## [2.8.10] - 2026-10-11

### 修复

- **标准音质播放（走转码/ffmpeg）的 lx 直链取流漏带浏览器 UA + 平台防盗链 Referer（bug B 同源遗漏）**。
  - 现象：在推荐歌单里**标准音质**播放时，保存目录出现一个 `.part` 临时文件很快消失，歌曲没落盘。
  - 根因：标准音质播放走 `/track/transcode`，代理用 **ffmpeg** 拉 lx 直链
    （`_transcode_source` → `transcode.py::_ffmpeg_argv` 的 `-headers`/`-user_agent`）。
    但 `_transcode_source` 只搬 `resolved["headers"]` 里的 referer/UA（**kw/kg 为空**），
    没补浏览器 UA 与平台 Referer → **kw/kg 的 CDN 反爬拒掉 ffmpeg 取流 → 转码失败**。
    即 2.8.7/2.8.8 的修复只覆盖了**流式下发** `_open_online_stream`，**漏了转码输入**这条路。
  - 修复：抽出共用函数 `_lx_stream_headers(guid, resolved)`（浏览器 UA + 按平台 Referer 表
    `_LX_REFERER`：`kw`→kuwo、`kg`→kugou；resolved 已带的不覆盖、Key 大小写归一），
    **流式下发与转码输入两条路都用它**，杜绝再次分叉。
  - 回归测试：`test_transcode_compat.py::test_lx_stream_headers_platform_referer_map`、
    `::test_transcode_source_lx_includes_platform_headers`。

## [2.8.9] - 2026-10-11

### 修复

- **收藏/最近播放的在线曲目「列表显示未知歌曲 + 永不绑定本地」的元数据缺口（加固）**。
  - 背景：即便音频字节已落盘（如 2.8.8 修好 kg 下载后），若**记录期**没抓到标题/歌手，
    收藏快照会存成空 → 列表显示「未知歌曲」、封面缺失，且**官方绑定被永久跳过**
    （曲目停留在 `online/lx/...` 在线引用态，看起来像"没落盘"）。
  - 根因：元数据回退链在**记录期**与**绑定期**不完整：
    - 收藏记录期 `_best_effort_online_info` 只查「搜索会话缓存 + 在线登记表 + 实时取流 +
      落地文件名」，**不查推荐/网易/广场歌单的磁盘缓存**（这类曲目从未走搜索，元数据只在缓存里）；
      而最近播放记录路径本就会查该缓存 → 所以"历史通常正常、收藏易缺"。
    - 绑定期 `_bind_official_task` 缺 title/artist 时**只回退查收藏快照自身**，不查登记表/歌单缓存
      → 快照一旦存空就不再尝试绑定。
  - 修复：
    - `_best_effort_online_info` 在现有兜底后，追加 `_lookup_playlist_cache_track` 回退
      （与最近播放记录路径同源）。
    - `_bind_official_task` 缺元数据时改为走完整回退链：在线登记表(REGISTRY) → 推荐/网易/广场
      歌单磁盘缓存 → 收藏/历史快照，命中才跳过绑定。
  - 回归测试：`test_official_bind.py::test_bind_falls_back_to_registry_when_snapshot_empty`
    （收藏快照存空、元数据只在 REGISTRY 时仍能完成绑定）。

## [2.8.8] - 2026-10-11

### 修复

- **酷狗（kg/kugou）在线曲目后台整轨下载失败、收藏/最近播放始终落不了盘（与 bug B 同类）**。
  - 现象：`journalctl -u fnmusic-ext` 里大量 `Background full fetch failed for online:lx:kg:...: RuntimeError`，
    tx/wy/kw 都正常落盘（`tee finalize done`），唯独 kg 反复失败；磁盘里也没有 `未知歌手 - 未知曲目*.mp3`
    之类的脏名文件——说明是**下载本身抛错**，不是元数据/绑定缺口。
  - 根因：2.8.7 的 bug B 修复只给 `lx:kw`（kuwo）补了 `Referer: https://www.kuwo.cn/`，**漏掉了
    `lx:kg`（kugou）。kugou CDN 同样防盗链，缺 Referer 时拒绝代理的 httpx 取流** →
    `_open_online_stream` 返回 None → 后台整轨下载抛 `open failed` RuntimeError → 永不落盘、
    永不绑定（收藏/最近播放里一直是 `online/lx/...` 在线引用态）。
  - 修复：把「平台 → 防盗链 Referer」抽成 `_LX_REFERER` 表（`kw`→kuwo、`kg`→kugou），lx 分支按
    平台补齐，与 kw 处理同款、结构性可扩展；tx/wy 不受影响。
  - 顺带把后台下载失败日志从裸 `RuntimeError` 改成带底层原因（`open failed` / `size mismatch` /
    `corrupt stream even at mp3 tier`），下次排查不必再靠猜。

## [2.8.7] - 2026-10-11

### 修复

- **酷我（kw）在线曲目在飞牛 App 里一播就跳下一首（bug B，根因实锤并修复）**。
  - 根因：解析与直链都正常（`docker logs` 全 200、背靠背直拉下满 46MB 已验证），
    但代理把音频流推给 App 那一步（`proxy/app.py::_open_online_stream` 的 lx 分支）
    只搬 `resolved["headers"]` 里的 referer/user-agent，而 **kw 的 resolved headers 为空**，
    流式 `build_request` 的 UA 是 httpx 默认 → **kuwo CDN 反爬挡非浏览器 UA** →
    取流校验不通过 → `_open_online_stream` 返回 None → App 跳曲。tx/kg 的 CDN 不挡
    httpx 默认 UA，故正常（这也解释了为什么只有酷我坏）。
  - 修复：lx 分支流式 fetch 前统一补浏览器 `User-Agent`，并对 kw 额外补
    `Referer: https://www.kuwo.cn/`（与代理封面段 kuwo headers 同款，见 `proxy/app.py:6240`）。
    属服务端改动、不依赖 App，低风险。
  - 注：若某 kw 曲目在源侧本身就是「试听源」、给不出可播直链，仍会失败——那是源本身
    限制，非代码可补；本次修复让**能出链的 kw 曲目**可正常播放。

- **设置页「平台歌单·启用平台」改为按音源实际支持动态过滤**。
  - 此前该勾选框的 5 个平台（QQ音乐/网易云/酷我/酷狗/咪咕）是 `app.js` 里写死的
    `ALL_PLATFORMS` 常量，**不读音源 `supportedSources`**。导致音源（如 ikun）根本不支持
    mg 时，UI 仍展示 mg 勾选项，用户勾了只会让代理静默注入失败。
  - 修复：`renderSourceChips` 现在按 `/api/status` 返回的 `lx_source.source.platforms`
    （音源真支持平台）过滤，隐藏不支持的平台并给出提示；无源信息时回退全量展示。
    纯前端改动，不依赖后端协议变更。

## [2.8.6] - 2026-10-11

### 修复

- **最近播放里重播「之前是在线搜出来的歌」时，播放界面显示「未知曲目/未知歌手」
  （但音频能播、歌词也正常）**（用户报 bug A）。
  - 根因：之前`在线曲目元数据登记表（REGISTRY）`只覆盖**推荐歌单
    （daily/hot/discover）/ 网易账号歌单**下发下来的曲目；**搜索结果里的在线曲目
    只活在内存 `_SEARCH_CACHE`**，从不登记进持久化 REGISTRY。代理重启、或
    lxserver 缓存把这条 id 逐出后，再播放（典型场景：最近播放）时 `track/metadata`
    只能从 lxserver 拿到合成的占位值「未知曲目/未知歌手」——音频是按 rid 直解析链、
    歌词是独立端点，所以两者都正常，唯独标题/歌手显示异常。
  - 修复：`search_track` 在计算出本页在线切片 `_online_window()` 后，把切片里的
    在线条目**一并 `record_many` 进持久化 REGISTRY**（`meta_from_track_vo` 提取，
    自动跳过本地/纯占位条目）。回填逻辑不变：只替换 title/artist/album/cover_url
    占位值，时长 `duration_s` 仍不回填（避免破坏转码 HLS 分片不变量）。
  - 新增回归测试：`test_search_results_register_metadata`、
    `test_search_track_replay_backfills_metadata`（清空 `_SEARCH_CACHE` 后
    仍能用登记值回填，隔离验证 REGISTRY 的贡献）。

## [2.8.5] - 2026-10-11

### 修复

- **改了「每个分类 N 个」但歌单数量不跟着变**（用户报：网易选 3 个分类每类 2 个，
  改成每类 1 个后歌单不减少）。
  - 根因：发现页卡片缓存 `_DISCOVER_CACHE` 的 key 只含 `source|cats`，**不含
    每分类条数（page_size）**。用户只改条数时 source 与 cats 都不变 → 命中 30 分钟
    的旧缓存 → 列表数量不变。
  - 修复 ①：`cache_key` 追加 `|{limit}`，条数一变立刻换桶。
  - 修复 ②：`.env` 热重载时，若变化键属于推荐歌单配置
    （`_DISCOVER_CONF_KEYS` = 开关/平台白名单/每平台数量/分类），立即
    `_reset_discover_cache()` 清空**卡片与曲目**两处缓存，不再干等 TTL。
- **酷狗（kg）分类选到「纯分组节点」时歌单为空，且分类栏列出一堆点了没内容的分类**。
  - 根因：kg 的 `tag/list?pid=0` 返回 10 个分组，但其中 **8 个（专区/心情/特色/
    场景/主题/语言/风格/乐器）的 `special_tag_id`/`song_tag_id`/`album_tag_id`
    全为 0**，且 `tag/list?pid=<自身>` 一律返回空 —— 它们是 kg 的虚拟归类节点，
    **本身不含任何歌单**。原先 UI 把它们原样列出，用户选中后必然空手而归。
  - 修复 ①：`_kg_categories()` 逐个用 `tag/specialList` 的 `total` 探测，
    **只保留真有歌单的分类**；有下发 `children` 的分组（年龄/长音频）则展开子项。
  - 修复 ②：`_kg_recommend()` 在指定分类取不到时**不再回落通用热门**——原先的
    回落会让「选了 A 分类却出现与 A 无关的歌单」，比空列表更误导。现在如实返回空。

- **酷我（kw）分类表列出一堆点了没内容的分类，且部分分类「名实不符」**。
  - 根因 ①：kw 的 `getTagList` 里「专区」组 17 个条目只有 5 个真能取到歌单
    （K歌/国风/铃声/佛乐/轻音乐），其余 12 个（经典老歌/网红/DJ/影视/动漫/
    新歌首发/小说/儿童/评书/综艺/古典/Vlog）上游返回 **0 条**。
  - 根因 ②：**分类 id 冲突**（实测 3 组）——`13` 既是「轻音乐专区」又是「粤语」、
    `12` 既是「国风专区」又是「小语种」、`143` 既是「铃声专区」又是「开心」。
    kw 取歌单接口对同 id 只有一份内容（实测 `13` 返回的是**粤语**歌单），
    若按「专区」组标签展示就会出现「选轻音乐专区却出粤语歌」。
  - 修复 ①：`_kw_categories()` 逐个探测内容，**只保留真有歌单的分类**。
  - 修复 ②：同 id 冲突时**取最后定义**（与 kw 实际语义一致），并按 id 去重，
    避免同名不同组的重复项。
  - 修复 ③：`_kw_recommend()` 的 `digest` 不再硬编码 10000，改为从分类表反查
    该 id 声明的 digest（查不到回落 10000），与 `_kw_resolve_digest()` 配套。

### 变更

- 推荐歌单缓存键与失效时机更精确：仅相关配置变化才清缓存，避免无谓重抓。
- kw/kg 分类表改为「只列真有内容的分类」，分类选择不再出现「选了必空」的选项。

## [2.8.4] - 2026-10-11

### 修复

- **「平台推荐歌单」的启用平台取消后不生效（反而注入全部平台）**：用户把设置里
  5 个平台全部取消勾选，期望「一个都不要」，结果分类栏仍在、歌单里仍有推荐歌单。
  - 根因：`_discover_sources()` 把「平台白名单为空」解释为**全部受支持平台**
    （`if raw else list(SUPPORTED_PLATFORMS)`）。于是在 UI 里取消所有平台 = 把
    `.env` 写空 = 代理理解为「注入全部 5 个平台」，与「取消」意图正好相反。
  - 修复：**空 = 不注入任何平台**，与总开关语义对齐。要「全都要」请显式勾选
    全部平台（UI 支持逐个勾选）。SCHEMA 标签、CONF 注释、`.env.example`、
    前端提示文案同步改写。
  - 该行为变更为**有意为之**：功能已有独立总开关 `FNMUSIC_RECOMMEND_PLAYLIST`，
    「一个平台都不选」应有明确表达方式，不该被隐式当成「全部」。
  - 回归测试 `test_discover_sources_empty_means_none`（替换原
    `test_discover_sources_defaults_to_all_supported`），已实测旧实现下失败、
    修复后通过；测试夹具相应改为显式启用全部平台以维持「有注入」前提。

### 变更

- **设置页：平台推荐歌单细节区随总开关收起**。总开关关闭时隐藏「启用平台 /
  分类筛选 / 每平台数量」，避免用户误以为「取消所有平台勾选」就能关闭功能。

## [2.8.3] - 2026-10-11

### 修复

- **修复 QQ 音乐（tx）歌单分类筛选完全失效**：用户选「国语」却出现
  「日本福冈 CIRCLE ′18 音乐节」「新奥尔良爵士与传承音乐节」等与分类无关的内容。
  - 根因：`platform_playlists._tx_recommend` 在指定分类时误用
    `get_playlist_by_category`。实测该 method 的 `id`/`titleid` **根本不参与分类过滤**
    —— 无论传什么 id，恒返回同一批固定歌单（`total` 恒为 **9**，恰好多为「音乐节」类），
    所以看起来"有结果、但不随分类变"。
  - 正解：统一走 **`get_playlist_by_tag` + `param.id = <分类id>`**。实测 `total` 随分类
    正确变化（国语 **4812**、流行 **5325**、民谣 **830**、经典 **764**、KTV热歌 **218**），
    返回内容与分类名一致（如「编辑推荐：国语热歌」「编辑推荐：国语新歌」）。
    空分类仍为 `id=10000000`（全部），非数字分类安全退化为「全部」。
  - 新增回归测试 `test_tx_recommend_uses_tag_method_with_category_id`：严格断言
    指定分类时走 `by_tag`、分类 id 进入 `param.id`、且**不得**再出现
    `get_playlist_by_category`；已实测该测试在旧实现下失败、修复后通过。
    （替换掉原先那条 `"11" in url` 式过松、且断言了错误行为的测试。）

## [2.8.2] - 2026-10-10

### 修复

- **修复 2.8.1 安装失败（构建上下文白名单漏放行）**：应用中心报
  `ERROR: failed to calculate checksum ...: "/proxy/platform_playlists.py": not found`。
  根因是 `.dockerignore` 首行 `*` 排除一切、再逐条 `!` 放行，2.8.1 只同步了
  Dockerfile 的 COPY 清单，**忘了在 `.dockerignore` 里放行 `proxy/platform_playlists.py`**
  → 该文件根本没进构建上下文，Docker 连 checksum 都算不出来。
  - `.dockerignore` 补上 `!proxy/platform_playlists.py`，并注明它必须与
    Dockerfile 的 `COPY ... /srv/proxy/` 清单保持一致。
  - 新增回归测试 `container/test_container.py::test_proxy_modules_allowed_in_build_context`：
    按 `.dockerignore` 的**逐行顺序语义**（`*` 排除 → `!` 放行 → `proxy/*` 再收窄）
    求出生效白名单，再与 Dockerfile 实际要 COPY 的模块名对齐，任一缺失即失败；
    同时反向检查"放行了却没人用"的冗余条目。

  **为什么这两次（2.8.0 / 2.8.1）都栽在同一处**：`/srv/proxy/` 的模块分发要同时满足
  **三个**位置一致 —— ① `webui-service/app.py` 的 import、② Dockerfile 的 COPY、
  ③ `.dockerignore` 的白名单。前两次各补了一个，这次把三个都锁进测试了。

## [2.8.1] - 2026-10-10

### 修复

- **修复 2.8.0 分类加载失败（镜像缺文件）**：容器内 WebUI 报「分类加载失败：分类模块不可用」。
  根因是 `webui-service/app.py` 里的 `from proxy import platform_playlists` 在容器中
  `ImportError`——镜像的 `/srv/proxy/` 只 COPY 了 `__init__.py` 与 `env_merge.py`，
  而 `/repo` 只是挂载进来的仓库、**不在 sys.path 上**，因此容器里取不到
  `platform_playlists`。
  - `container/Dockerfile` 的 `/srv/proxy/` COPY 清单补上 `platform_playlists.py`。
  - 该导入失败的兜底文案改为**原样带出异常信息**（原来只写「分类模块不可用」，
    排查时无从下手）。
  - 新增回归测试 `container/test_container.py::test_webui_proxy_imports_ship_in_image`：
    解析 `webui-service/app.py` 里所有 `from proxy[.x] import ...`，
    逐一核对 Dockerfile 是否 COPY 进 `/srv/proxy/`。
    本机单测跑在仓库根目录、能 import 到完整 proxy 包，**"测试全绿但线上必炸"**
    正是这次事故的形态，该断言专门堵这一类。

## [2.8.0] - 2026-10-10

### 新增

- **平台歌单分类筛选**：设置页「平台推荐歌单（发现）」现支持按平台、按分类多选，
  不再只有「全部」一档。
  - **平台白名单由文本框改为勾选框**：QQ音乐 / 网易云 / 酷我 / 酷狗 / 咪咕 五个
    平台一键勾选，全部不选 = 全部平台（与原行为一致）。
  - **分类多选**：勾选平台后，其下按平台原生分组展示分类标签，可任意多选。
    配置项 `FNMUSIC_RECOMMEND_PLAYLIST_CATEGORY`（csv，格式 `<平台>:<分类id>`，
    如 `tx:11,tx:194,kg:12`），留空 = 各平台「全部」。
  - **取数策略**：勾选多个分类时，每个分类各取 `recommend_playlist_page_size`
    条后按歌单 id 合并去重（同一歌单可能挂在多个标签下）。
  - 咪咕无分类体系（只有运营楼层），不参与分类筛选；其平台勾选仍有效。

- **各平台分类接口接入**（`proxy/platform_playlists.py`，2026-10 实测）：
  - **tx**：`c.y.qq.com/.../fcg_get_diss_tag_conf.fcg` 取官方分类树（6 组 60+ 分类）；
    按分类取歌单改用 `get_playlist_by_category`（`get_playlist_by_tag` 不接受分类 id）。
  - **kg**：`tag/list` 取父分类；取歌单前先经 `tag/info` 把分类 id 解析成
    `special_tag_id`，再走 `tag/specialList`。
  - **kw**：`getTagList` 取分类；按分类取歌单走 `getTagPlayList`。
  - **wy**：`playlist/catlist` 已下线（404），分类名固化在内置表；
    取歌单走 `playlist/list?cat=<中文分类名>`（实测 cat 参数有效）。
  - 动态拉取失败一律回落内置兜底表，保证 UI 分类区不消失。

- 新增代理端点 `GET /_ext/playlist-categories`（`source` 空则返回全部平台），
  供 WebUI 与外部工具查询分类。
- 新增 WebUI 端点 `GET /api/playlist-categories`（1 小时进程内缓存），
  直接复用 `proxy.platform_playlists` 的分类逻辑，避免两处实现漂移。

### 测试

- `proxy/tests/test_discover_playlists.py` 新增 6 例：分类 csv 解析、
  逐分类抓取合并去重、无分类时零行为变化、tx method 切换、兜底表回落、
  分类平台范围。
- `webui-service/test_webui.py` 新增 4 例：单平台分类、全平台分类、
  不支持平台返回空、上游异常不 5xx。
- `webui-service/test_app_js.js` 的平台歌单用例改为覆盖 chip 多选状态映射。

## [2.7.12] - 2026-10-10

### 修复

- **广场歌单「列表」前十几首曲目没有封面（点进详情却有）**：2.7.11 修好卡片
  封面后暴露出同源问题的第二半。App 进歌单先拉一次曲目列表拿到一批
  `coverId`，这些 `coverId` 由 `disguise_client_json()` 伪装成
  `track_<32hex>`；fake ↔ real 的映射**只存在进程内 `_FAKE_GUID_REVERSE`**，
  **一重启即全部丢失**。而 `ensure_registry_warm()` 只扫
  `fav_dir` / `plt_dir` / `play_history_dir` / `recommend_cache_dir` /
  `nmpl.cache_dir()` 五个目录 —— 广场曲目此前**只写进程内
  `_DISCOVER_TRACK_CACHE`、从不落盘**，warm 无从重建 → App 持重启前的
  `coverId` 取封面时 `resolve_real_guid` 反解失败 → 落到非 online 兜底分支 →
  返回 `{"code":0,"data":{"list":[],"total":0}}`（一段 JSON）→ 图片解码失败 →
  无封面。**「前十几首」正是 App 首屏批量请求的那一批**；点进详情会重新走
  列表接口、把 fake↔real 重新登记回内存表，所以**详情有、列表没有**。
  - 新增广场曲目落盘：`_discover_track_dir()` / `_discover_track_path()` /
    `_save_discover_tracks()` / `_persisted_discover_tracks()` /
    `_prune_discover_tracks()`，落盘于
    `<recommend_cache>/discover_tracks/<source>__<playlist_id>.json`
    （与 `recommend_cache/<user>/*.json` 同款结构，`_iter_registry_jsons()`
    天然可扫）。文件数超 256 时按 mtime 最旧清掉一半。
  - `_load_discover_tracks()`：拉取上游成功即落盘；上游不可用时回落
    `_persisted_discover_tracks()` 兜底（重启后离线仍能出列表与封面）。
  - `ensure_registry_warm()` 目录元组新增 `_discover_track_dir()`，重启后
    可重建广场曲目的 fake↔real 映射。

### 测试

- 新增 `test_discover_tracks_persisted_to_disk`：断言拉取曲目列表后落盘文件
  存在且字段完整（source / playlistId / tracks / guid 前缀）。
- 新增 `test_discover_track_cover_survives_restart`：严格模拟重启（清空
  全部进程内表与 fake 映射、复位 warm 标记），验证每首曲目仍能 302 到
  **各自的**封面直链。

## [2.7.11] - 2026-10-10

### 修复

- **「歌单广场」推荐歌单列表与详情全部没有封面（占位图）**：App 取封面走的是
  `coverId`，而 discover 卡的 `coverId` 是一个由**封面直链**派生的假 32-hex
  （`_discover_cover_id` → `fake_official_guid(cover_url)`）。`/static/cover`
  入口先做 `extract_guid` → `resolve_real_guid`，于是 coverId 被反解成
  `http://p.qpic.cn/...` 这样的**直链**；拿直链去做
  `is_discover_playlist_guid()` 必然为假 → 整块 discover 分支被跳过 →
  封面退成占位图。
  - 老测试只覆盖 `?guid=online:playlist:discover:...` 路径（该路径
    `resolve_real_guid` 反解不出、原样返回，判定为真），所以一直全绿；
    **App 实际使用的 coverId 路径没有任何用例**，bug 就这样藏了很久。
  - 新增 `_resolve_discover_guid()`：按「resolve 后 guid → 原始候选值 →
    候选值再 resolve → coverId 反查歌单 guid」四步判定归属，任一命中即按
    discover 处理。
  - 新增 `_discover_guid_by_cover()`：coverId → 歌单 guid 反查，先查进程内
    正向表，再查落盘卡片索引。
  - `_load_discover_card_index()` 加载索引时**顺带重建封面反查表**
    （`_DISCOVER_COVER_REVERSE` / `_DISCOVER_COVER_FORWARD`）。fake 是单向
    哈希，不 warm 就永远反解不出——这修掉了「重启后 App 持旧 coverId 取封面
    必然失败」的窗口。
  - 实测确认 5 个平台（tx/wy/kw/kg/mg）共 60 张卡片**全部带封面直链**、
    且代理侧均能成功下载（HTTP 200），故「无封面」纯属渲染侧问题。

### 测试

- 新增 `test_static_cover_by_coverid_redirects`：覆盖 App 实际使用的
  `coverId` 路径，含带 `size` 参数与 `/<coverId>` 路径两种形态。
- 新增 `test_static_cover_by_coverid_survives_restart`：模拟重启（清空全部
  进程内表、保留落盘索引），验证 coverId 仍能出图。
- `test_playlist_detail_returns_discover_card` 补详情页 coverId 出图断言。

## [2.7.10] - 2026-10-10

### 修复

- **「热门推荐」歌单长期为空（`trackCount=0`）→ 连带表现为「推荐歌单没有封面」**：
  lxserver 的 `/api/music/leaderboard/list` **只认 `bangid` 查询参数**，传 `id=`
  一律返回 `Missing bangid`（非 200）；而 `lxmusic-service/app.py` 硬编码的是
  裸数字 `{"kg":"8888","kw":"93","wy":"3779629"}` 并作为 `id=` 发出 →
  `get_leaderboard_list` 撞上 `if resp.status_code != 200: return []` 静默返回空 →
  洛雪 `/api/v1/recommend` 返回 `{"items":[],"errors":{}}` → 代理 `from_lx_charts`
  拿到空列表 → hot 歌单 0 首。歌单没有曲目，封面自然只剩占位图。
  - `get_leaderboard_list` 改用 `bangid=` 传参（2026-10 实测的唯一可用契约）；
  - 新增 `resolve_leaderboard_bangid()`：运行期先查 `/api/music/leaderboard/boards`
    取该平台真实 `bangid`（可按榜单名匹配），取不到才回落内置默认值 ——
    平台调榜时硬编码不再静默失效；
  - `recommend_charts` 不再硬编码榜单 ID，改用上述解析结果。

- **整条榜单链 4 处静默吞异常，让上述故障完全不可见**（用户端 healthz 只有
  `tiers:[]` + `tierFailures:[]`，毫无线索）：
  - `fetch_lx_charts` 不再丢弃洛雪返回的 `errors` 字典（改为 warning 透出，
    并区分「空 items / 载荷畸形 / 上游 errors」三种情形）；
  - `from_lx_charts` / `from_netease_charts` 的静默 `return []` 记入
    `tier_failures`（`unavailable` / `empty` / `no-platform-overlap`），
    healthz 现在能显示「是哪一层缺席、为什么」；
  - `get_leaderboard_list` 非 200 打 warning 并带状态码与响应体摘要；
  - `recommend_charts` 中「取到 200 但列表为空」也记入 `errors`（此前只有异常才记）。

### 修复（可观测性）

- **`takeover.py` 脱敏白名单误伤诊断行**：代理日志并非直接进 journal，而是经
  `safe_child_log()` 的 `_SAFE_OUTCOME` 白名单过滤后转发。`=== fnmusic-ext v...
  configuration ===`、`[自检] 曲库目录 / 边听边存落盘目录`、`cover probe:`、
  `playlist cover ... placeholder`、`曲库自动探测命中` 等行**全被丢弃**，
  导致 `diagnose_playback.sh` 的自检段与 5a/5b 段**恒为空**，排查时反复误判成
  「升级没生效 / 跑的是旧代码」。现已把上述固定格式的观测行加入白名单
  （均为固定前缀、不含凭据；显式注入的 secret 仍会被替换）。

### 测试

- `test_recommend_endpoint` 从「只断言 `isinstance(items, list)`」改为
  **断言真的取到曲目 + 传给 lxserver 的是 `bangid` 而非 `id`** —— 此前空榜单
  在测试里永远绿，是本故障得以长期潜伏的原因之一。
- 新增 3 例 `bangid` 契约回归（请求参数断言、非 200 需告警、按名解析 bangid）。
- 新增 `safe_child_log` 观测行放行测试（含凭据反例，确保未扩大攻击面）。

## [2.7.9] - 2026-10-10

### 修复

- **曲库探测在应用中心（fpk）安装形态下必然失败 → 边听边存"看起来彻底失效"**：
  `FNMUSIC_MUSIC_DB` 的默认值 `/usr/local/apps/@appdata/trim.music/db/music.db`
  是官方音乐 App 的**容器内视角**路径。fpk 形态下拦截代理跑在 NAS **宿主机**的
  systemd 服务里，这个路径在宿主机上并不存在 → `detect_library_dir()` 静默回落到
  `cache` 目录：文件其实落了盘，但**文件名是哈希、不进官方曲库**，用户在飞牛音乐
  里当然一首也找不到。修复：
  - 配置的 `music.db` 落空时，按飞牛真实目录布局再探测一遍
    （`/vol*/@appdata/trim.music/db/music.db`、
    `/usr/local/apps/@appdata/trim.music/db/music.db`、
    `/vol*/@appdata/trim.music/db/*.db`），命中即用并打 INFO 留痕
    （`曲库自动探测命中：music.db=...`）；多个候选逐个尝试，某个候选表缺失或
    db 文件是坏的时候不会中断后续候选；
  - 官方曲库反查（`official_track_guid_by_tags` / `library_file_by_tags`）同样改走
    `resolve_music_db()`，否则这两条链在宿主机部署下会因为默认路径不存在而直接放弃；
  - **启动日志新增自检两行**：`[自检] 曲库目录 = ...` 与
    `[自检] 边听边存落盘目录 = ...`。tee 只在播放在线曲目时才触发，出问题时用户
    现在只要看一眼启动日志就知道文件会落到哪，不必等第一次播放。
- **洛雪源历史数据迁移自举彻底失效**：`lxmusic-service/app.py` 的
  `_bootstrap_migration()` 用了 `pathlib.Path` 却从未导入，真机日志里长期只有一句
  `历史数据检查与迁移异常: name 'Path' is not defined` —— 历史自定义源脚本迁移、
  历史音源自动激活**全被这一句 except 吞掉**。已补导入。
- 同一函数还误调用了不存在的 `LXSERVER.health()`（客户端只有 `is_alive()`）：
  异常被吞掉 → 白等 20×0.5s 且**根本没有等 lxserver 就绪**就开始迁移。改回
  `is_alive()`。

### 变更

- **`scripts/diagnose_playback.sh` 重写日志与配置来源（重要）**：此前脚本只读
  `docker logs fnmusic-sources`，而 `stream probe`（取流探针）、`tee finalize done`
  （边听边存落盘）、`cover probe` / `playlist cover`（封面）这些行全部由**宿主机上
  的代理**写出、进的是 **systemd journal** —— 只看容器日志永远抓不到，这直接导致
  了"升级后看不到任何探针行、无法判断修没修好"的误判。现在：
  - 新增第 1 节「宿主机代理服务与端口」（`systemctl is-active/show/status` +
    `ss -ltnp` 看 8768/8770/8772/8774）；
  - 新增第 2 节「宿主机 repo/.env」：打印代理真正读取的那份配置（只打白名单键值
    与键名，不打印可能含账号/token 的值），并明确说明
    **`docker exec ... env` 看不到 `FNMUSIC_*` 是正常的**（compose 没有
    `env_file`，容器内是从挂载进来的 `/repo/.env` 读开关）；
  - 第 2b 节改为宿主机侧探测：列出 `/vol*/@appdata/` 下 trim/music 相关项、逐个判断
    候选 `music.db`、用 `sqlite3`（无则 `python3`）读 `shared_library` 并标注每条
    路径是否可用、列出宿主机侧落盘候选目录与封面缓存规模；
  - 第 5 节拆成 5a（启动配置与曲库自检）/ 5b（边听边存）/ 5c（封面）/ 5d（告警），
    全部取自 journal；容器侧日志移到第 6 节并注明"这里不会有 tee/cover probe"；
  - 报告末尾新增第 9 节「怎么读这份报告」：按"一点就跳下一首 / 边听边存失效 /
    列表没有封面"三种症状直接对号入座，并写明**某段为空 = 升级后还没重放操作**
    （探针只在真的发生请求时才写）。
- `FNMUSIC_MUSIC_DB` 的 `.env.example` 注释补上宿主机视角的示例路径
  （`/vol1/@appdata/trim.music/db/music.db`）。

### 测试

- 新增 `proxy/tests/test_library_dir_detect.py`：覆盖显式 `FNMUSIC_LIBRARY_DIR` 优先、
  配置 db 命中、宿主机布局兜底并留 INFO、INFO/WARNING 只打一次、坏 db 容错、
  `resolve_music_db()` 优先级、启动自检两行。
- `lxmusic-service/test_client_and_app.py` 新增 5 个迁移自举用例：`Path` 已导入、
  历史 `uploads/*.js` 迁移上传（同名跳过）、历史 `state.json` 的源自动激活、
  已有激活源不被历史配置覆盖、空环境跑完不报异常。
- 顺手给依赖 POSIX 绝对路径的 `test_source_set_activates_unknown_local_file_by_import`
  加 `os.name != "posix"` 跳过守卫（Windows 下 `urlparse("file://D:////...")` 会把盘符
  并进 path，该用例在 Windows 必然红，与本次改动无关）。

## [2.7.8] - 2026-10-10

### 修复

- **歌单（每日推荐 / 热门推荐 / 网易账号歌单）完全没有封面图**：`/static/cover`
  的歌单分支在"快照里找不到可用封面"时**直接 404**，客户端只显示裂图/空图标。
  而**在线音源构建的推荐歌单整单都不带 `cover_url`、`coverId` 又是 `online:`
  形态**，`pick_playlist_cover_track()` 必然返回 None —— 于是整张歌单封面从未
  显示过。这与文件里早就写明的原则（"在线曲目封面永不 404，空封面是客户端
  裂图的主要来源"）自相矛盾，歌单封面此前没有被这条原则覆盖。修复：
  - 歌单封面出口补全为一条完整兜底链：**快照自带 `cover_url`（代理/302）→
    首曲在线曲目走在线封面链（上游封面 → 在线登记表 → kw rid/qq albummid
    直构 → 网易补全 → 本地内嵌图）→ 本地官方 `coverId` 透传（上游返回
    ≥400 时不再原样透传坏状态）→ 占位图池**。任何一步成功即返回，**绝不再 404**；
  - `pick_playlist_cover_track()` 返回 None（整单无现成封面）时，改用**第一首
    在线曲目**作为封面来源，交给在线封面链处理；
  - 网易账号歌单封面直链未登记（重启后反查表未重建）时同样回落占位图，不再 404；
  - 在线曲目封面链读取上游详情后**再过一遍在线登记表**（`_registry_apply`）：
    登记表里有封面直链而上游没有时，此前会白白走到占位图。

### 变更

- **修复 Windows 打包脚本在中文控制台下崩溃**：`packaging/fpk/build_win.py`
  在最后一步打印「行尾门禁 … ✓」时，cp936 控制台无法编码 `✓` →
  `UnicodeEncodeError` 直接中断（前后多次打包都卡在这一步，包其实已经生成、
  但退出码为 1 且 sha256 行不打印）。`log()` 改为编码不可用时降级替换输出。
- **封面代理新增失败短时记忆**：某个封面 URL 取回失败后 10 分钟内不再重试
  （直接回落 302 由客户端自取），避免一个不可达/防盗链的 CDN 让整页封面
  每张都白等满 8 秒超时。
- **新增封面探针 `FNMUSIC_COVER_PROBE`（默认开）**：记录每条 `/static/cover`
  请求最终落到的形态（NAS 本地文件 / 302 直链 / 官方透传 / 占位图），歌单封面
  退占位图时另打一行说明原因，便于真机定位"某个列表没封面"。
- `scripts/diagnose_playback.sh` 新增 **5b 封面探针摘要段**：按产物类型列出最近
  的封面请求结果、歌单封面兜底记录、封面缓存目录规模与占用。

### 测试

- 新增 `proxy/tests/test_playlist_cover.py`（8 个用例）：整单在线曲目无封面
  不再 404、登记表封面可被歌单封面采用、本地 coverId 上游 404 回落占位图、
  上游 200 原样透传、快照 cover_url 走代理/302、网易歌单无封面回落占位图、
  负缓存不重复取回、封面探针日志。
- 更新两处**断言旧 404 行为**的用例（歌单无封面 → 占位图，属有意的行为变更）：
  `test_nm_playlists.py::test_cover_redirect`、
  `test_v2_features.py::test_static_cover_playlist_without_covers_falls_back_placeholder`。
- 加固两个编码敏感用例（Windows 非 UTF-8 模式下会因 cp936 抽风失败，CI 不受影响）：
  `webui-service/test_app_js.py` 显式 `encoding="utf-8", errors="replace"`；
  `test_version_env.py` 临时 .env 显式 UTF-8 写入。
- 全量：`proxy/tests` + `webui-service` 在 UTF-8 模式下 **534 passed / 55 skipped /
  0 failed**；PowerShell（cp936、node 可用）下 webui 61 passed / 2 skipped。

## [2.7.7] - 2026-10-10

### 新增

- **封面代理 + NAS 落盘缓存**（修复「手机 App 封面正常、电脑浏览器封面经常加载不出来」）：
  根因不在扩展缓存，而在浏览器行为 —— Chrome 86+ 会对 http 图片做 mixed-content
  自动升级（https），部分音源 CDN 不支持 https，升级失败后图片被**静默丢弃**；
  手机 App 不受 mixed-content 约束，所以同一首歌 App 有图、网页没图。
  - `/static/cover` 全部 7 处外部 302 点（发现页卡片×2、网易账号歌单、专辑锚点、
    直链、kw rid/qq albummid 直构、网易补全）统一改为：**本地缓存命中 → 直接回
    NAS 文件；未命中 → 代理代取封面字节并原子落盘；彻底失败 → 回落原 302 直链**
    （行为与旧版完全一致，绝不因代理故障而 404）；
  - 缓存目录 `FNMUSIC_COVER_CACHE_DIR`（默认 `<cache>/covers`），按 URL sha256
    命名 + 魔数嗅探扩展名，数量超 `FNMUSIC_COVER_CACHE_MAX`（默认 2000）按最旧
    mtime 淘汰；总开关 `FNMUSIC_COVER_PROXY`（默认开），三项均支持 .env 热重载；
  - WebUI「边听边存」分组新增「封面代理缓存（NAS）」开关；`.env.example` 与
    README 配置表同步补齐；
  - 新增 `proxy/tests/test_cover_proxy.py`（11 个回归用例：缓存读写/淘汰、开关
    关闭直连、取回失败回落 302、二次请求纯缓存命中断网验证、kw 文本假图/非
    http 拒代理）。

### 修复

- **边听边存「静默降级」无任何提示**（用户反馈"不知道存哪儿了/压根没存"的定位成本来源）：
  曲库探测（`detect_library_dir`）读不到飞牛 music.db 或 shared_library 无可用
  路径时，落盘目录静默回退到 cache 目录 —— 文件仍在落（哈希名），但**不进官方
  曲库**，看起来就像功能失效。现在该回落会打 WARNING（music.db 缺失 / 无可用
  路径两种情形分别给出 .env 修法：`FNMUSIC_MUSIC_DB` 或 `FNMUSIC_LIBRARY_DIR`），
  shared_library 里有路径但全部不可访问时同样留痕。

### 变更

- `scripts/diagnose_playback.sh` 新增 **2b 边听边存诊断段**：打印 tee/曲库/封面
  相关全部环境变量、music.db 存在性与 shared_library 路径、三个落盘候选目录
  的最近文件、封面缓存目录规模；日志段 grep 增补 tee/finalize/cover/曲库/落盘
  关键词，并附「边听边存关注这几行」速查（stream probe 的 range 形态与
  tee_eligible、tee finalize done 的 dest 路径、Background full fetch、曲库
  探测告警、Library scan 触发）。边听边存疑似失效时，跑一次该脚本把输出贴回
  即可定位是「开关被关 / 音质走转码 / 曲库探测回落」中的哪一种。

## [2.7.6] - 2026-10-10

### 修复

- **「最近播放」「收藏」列表里仍显示「未知歌曲 / 未知歌手」（2.7.5 元数据修复的漏网）**：
  2.7.5 只把在线登记表接进了**落盘链路**（曲库定名/文件标签/下载），而**列表接口**读的是
  另一份数据 —— 本地存储的**历史快照**，渲染时从不查登记表。

  数据流对照（这是"改一处漏一处"的根源）：

  | 出口 | 元数据来源 | 2.7.5 是否已接登记表 |
  | --- | --- | --- |
  | 播放详情 `/track/metadata` | 上游 `/info` + 登记表 | ✅ |
  | 落盘/下载（定名、写标签） | `_info_for_background_save()` 等 | ✅ |
  | **最近播放 `/play-history/list`** | `play_history/<user>.json` 快照 | ❌ |
  | **收藏 `/favorite-track/list`** | `online_favorites/<user>.json` 快照 | ❌ |
  | **官方歌单附加条目** | `playlist_tracks/<user>.json` 快照 | ❌ |
  | **写入侧** `POST /event/report` | 上报 payload + 搜索会话 + 歌单缓存 | ❌（不查登记表） |

  两个独立缺陷叠加：
  ① **列表渲染只读旧快照**：`build_favorite_track_obj()` 直接拿快照构造下发 VO，
  从不回查登记表，也不做占位值判定 —— 旧快照里的「未知曲目/未知歌手」会一直显示下去；
  ② **上报写入不带元数据**：`event/report` 处理 `track_play` 时以空 `stub_online_info()`
  为底，只从上报 payload / 搜索会话 / 歌单磁盘缓存补齐，**从不查登记表**；而推荐歌单、
  平台推荐歌单、网易账号歌单的曲目**从不经过搜索**，搜索会话必然落空 → 历史快照自带
  占位/空值，随后被列表渲染固化。

  修复（写入与渲染两侧同时收口，避免再漏）：
  - `build_favorite_track_obj()` 内接入登记表回填 —— 所有"快照 → 下发 VO"的**单一收口点**
    （收藏列表、最近播放、歌单附加条目、收藏创建、歌单添加共用）；
  - 新增 `_heal_stored_meta()`：列表渲染期两级自愈，在线登记表 → 推荐/网易账号歌单磁盘缓存
    （登记表按 14 天 TTL 轮换，过期后仍能从歌单缓存还原真名）；两级都查不到时保持原样，
    绝不臆造；
  - 最近播放列表的空值判定从「标题是否为空」改为 `_is_real_tag()`（**占位即缺失**），
    补齐用 `_merge_missing_tags()` —— 上游占位值不再挡住回查到的真名；
  - `event/report` 写入前先过登记表，合并一律走 `_merge_missing_tags()`；**落库前剥离占位值**
    （存空值而非"未知曲目"，交给列表期自愈再次尝试补全，避免脏值把兜底链一次钉死）；
  - `_best_effort_online_info()`：不再拿**占位式文件名**（≤2.7.4 落库的
    `未知歌手 - 未知曲目.mp3`）当元数据写进收藏/歌单快照，占位值绝不扩散；
  - `_lookup_online_snapshot()`：优先返回带真实标题/歌手的快照 —— 播放历史里的占位旧快照
    不再挡住收藏/歌单里的真名；
  - 聚合专辑详情（musicdl/lx）候选先过登记表回填再按专辑名归组，专辑页标签页同样受益。

### 开发

- 新增「元数据出口矩阵」回归用例（`proxy/tests/test_transcode_compat.py` +8 例）：
  收藏列表 / 最近播放列表 / 官方歌单附加条目 / 播放详情 / 上报写入，用**同一份脏数据**
  （占位值快照 + 上游只返回占位值）打全部出口，逐条断言必须给出登记表里的真名。
  任何出口（含后续新增的）漏接回填都会直接失败。
  反向验证：把 `_registry_apply()` 短路成恒等后，`build_favorite_track_obj` 回填、
  历史上报回填、出口矩阵三例全部失败。
- **本地测试提速与去噪（Windows 开发机；CI/Linux 不受影响）**：此前本机全量
  `pytest proxy/tests` 恒定 36 failed（全是平台差异：POSIX shell 片段、AF_UNIX、
  0600 权限位、fcntl、假 ffmpeg 的 shebang 执行），每轮都要人工比对"36 基线"，
  且最慢的 `test_install_edges.py`（18 例，约 4 分钟）全部必败。现按类收口：
  · `conftest.py` 对 POSIX 专属三文件（fcntl/UDS 进程接管）`collect_ignore`；
  · `test_install_edges` / `test_webui_gateway` 整文件 `skipif(win32)`；
  · `test_version_env` 两例权限位断言、`test_platform_table` 的 bash 菜单执行用例
    单独 `skipif(win32)`；
  · `fake_ffmpeg` fixture 统一做可执行性探测，不能直接 exec .py 的平台整组 skip
    （原来 8 例恒红，只 1 例手工 skip）。
  效果：本机全量 5m25s/36 failed → **1m23s/0 failed**（472 passed / 55 skipped），
  失败清单不再需要 `--tb=line` 补捞。musicbox 的 2 例扫码用例是本机 venv 缺
  `qrcode`/`pillow`（CI 本来就装），补装即绿。

## [2.7.5] - 2026-10-10

### 修复

- **下载到本地 / 边听边存落库的曲目显示「未知歌手 - 未知曲目」（2.7.3 元数据修复的漏网）**：
  2.7.3 只把在线登记表接到**播放详情**（`_online_info()`），而**落盘链路**（曲库定名、
  写文件标签、下载产物打标签）走的是另一条元数据解析路径 —— 既不查登记表，也只判
  「字段非空」。

  后果：上游（lxserver 未命中缓存时）合成的占位常量 `未知曲目` / `未知歌手` 被当成有效
  元数据 —— 文件以 `未知歌手 - 未知曲目.<ext>` 落进曲库，写进文件的标签也是这两个占位值，
  App 下载到本地后就按这个名字与标签显示。播放界面正常（走的是已修好的详情链路），
  所以症状表现为「播放没事、下载到本地是未知」。

  修复：
  - 新增 `_registry_apply()`（登记表回填**统一出口**）与 `_has_real_title()` / `_is_real_tag()`：
    「有标题」的判定排除上游占位值；所有落盘点（`_info_for_background_save`、
    `_merge_missing_tags()` / `_tee_metadata_fallback()`、下载打标签）一律先过登记表回填；
  - 元数据回查优先级：在线登记表（歌单下发时的真实值）→ 推荐/网易账号歌单磁盘缓存 →
    播放历史/收藏/自建歌单；占位值按**缺失**处理，因此回查到的真名能覆盖上去；
  - 下载链路（`/music/api/v1/download/track/transcode/*`）：
    · 落盘源先经 `_dl_tagged_media()` 自愈 —— 补写真实标签，文件名仍是占位式时**同步改名**
      （同名 `.lrc` 随迁、`.ref` 更新、通知官方重扫）。**滚动缓存文件不原地改写**（可能正被
      播放读取），改为另存一份带标签的副本供下载；解析不出真名时原样供文件，绝不制造半成品；
    · 标准档转码产物此前被 `-map_metadata -1` 清空标签后**没有补写**（下载到本地完全没有
      标签），现由 `_dl_tag_transcoded()` 补写真实元数据；
    · 下载响应的 `Content-Disposition` 改用真实元数据文件名（转码档此前给的是 guid 哈希名，
      原始档给的是曲库名 —— 可能本身就是占位式），App 存到本地后即为「歌手 - 歌名.ext」。
  - 存量数据：`scripts/repair_unknown_library.py` 的覆盖面从 `unknown(n)` 扩展到**占位式
    文件名**，并接入在线登记表作为元数据来源（默认仍是 dry-run 预览，`--apply` 才动手）。

### 开发

- `online_registry` 新增公开出口 `is_placeholder()` / `is_placeholder_name()` / `snapshot()`：
  占位值判定收拢到一处，app 侧下载自愈与存量修复脚本共用同一规则。
- `proxy/tests/test_transcode_compat.py` 新增 10 例：登记表回填优先于上游占位值、`_tee_finalize`
  按真名落盘、下载自愈改名与 `.lrc` 随迁、查不到真名时不改写、滚动缓存出带标签副本、
  下载端点响应名、转码产物打标签（抽出的 `_dl_tag_transcoded()` 直接单测，不依赖真 ffmpeg）；
  `proxy/tests/test_online_registry.py` 新增 3 例。
  反向验证：把 `_registry_apply()` 短路成恒等后，上述 5 个承重用例全部失败。

## [2.7.4] - 2026-10-09

### 修复

- **应用中心重装/升级 FPK 后音源被静默重置回 musicdl 聚合音源（严重，推荐歌单曲目
  全部「一播就跳下一首」的最终根因）**：两个独立缺陷叠加所致。

  ① **fpk 钩子读 `.env` 对不上真实格式**（`packaging/fpk/cmd/_common`）：
  install.sh 与 WebUI 写出的值一律经 `env_merge.quote_env_value()` **单引号包裹**
  （`FNMUSIC_LX_ENABLED='true'`、`LX_SOURCE_URL='http://…'`），而
  `fnmusic_env_source()` 只认裸 `KEY=true`、`fnmusic_saved_lx_url()` 不剥引号。后果：
  · `fnmusic_saved_source()` 对真实 `.env` **恒返回空** → `upgrade_callback` 只能回落
  `musicdl`，用户选好的音源在每次升级后丢失；
  · `fnmusic_saved_lx_url()` 返回的带引号 URL 传给 install.sh `--lx-source-url` 时不匹配
  `http(s)://` / `file://` 分支，被当成「本机 .js 文件路径」→ `log_err` **直接中止升级**。

  ② **`install_callback` 从不参考已安装音源**：它只读向导值 `wizard_sources`，取不到就
  **硬编码回落 `musicdl`**；而向导字段是 `required` + `initValue="musicdl"`，同版本重装 /
  App Center 覆盖安装（都走安装钩子）时会提交默认值。叠加 install.sh 把
  `FNMUSIC_MUSICDL_ENABLED` / `FNMUSIC_LX_ENABLED` 当作「安装时部署选项」**永远以本次
  安装值覆盖 `.env`**，用户设好的洛雪自定义源就被改写回 musicdl 聚合音源。

  现象之所以具有迷惑性：曲目元数据（歌名/歌手/封面）由在线登记表提供，**音源被切走也照
  常显示**；但推荐歌单的曲目 id 是 `lx:` 形态，`_source_enabled()` 判定为未启用 → 取流
  直接 404 → App 一播就跳下一首。这也解释了此前几轮「歌名正常、一播就跳」的反复。

  修复：
  - `fnmusic_env_source()` 统一剥离 `export` 前缀 / 单双引号 / 空白（含 CRLF 的 `\r`）后
  再比对，兼容 `true` / `1` / `yes`；`fnmusic_saved_lx_url()` 同样剥引号；
  - `install_callback` 改为**本机已安装时一律沿用已安装音源**：优先 `.env`
  （管理页切源即写这里，最新），其次 `install_init` 在新包落盘**之前**写入的持久快照
  （`/var/lib/fnmusic-ext/installed-source` —— 重装会重建 `target/`，`.env` 可能先被清掉）。
  沿用洛雪源时与 `upgrade_callback` 对齐，带上已保存的源地址并 `--lx-skip-verify`，
  避免源服务器临时故障让重装直接中止；向导值与实际采纳值都会写进持久安装日志；
  - `uninstall_callback` 清掉该快照，使「卸载后重新安装」仍回到向导的初始音源选择。
  向导字段本就叫「初始音源」、说明也写明「安装后可随时在管理页切换」，因此重装不再覆盖
  用户选择：想换音源请用管理页，或先卸载再安装。

### 开发

- `packaging/tests/test_fpk_cmds.py` 新增 6 例：重装沿用已装音源、`.env` 被清空时用快照
  兜底、首次安装仍按向导、`install_init` 写快照、`uninstall_callback` 清快照、
  `.env` 引号与 CRLF 兼容。并把 `test_common_saved_source_priority` /
  `test_common_saved_lx_url` 的用例改为**真实的单引号格式** —— 此前用例写的是裸
  `KEY=true`，恰好绕过了这个 bug（测试与实现犯了同一个错）。
- 新增 `scripts/verify_fpk_source_keep.sh`：不依赖特权软链沙箱校验 fpk 钩子的音源保持
  行为（21 项断言：`.env` 格式兼容、四种 `install_callback` 组参场景、快照读写与清理）。
  在 Windows（Git Bash）/ Linux 均可运行；`packaging/tests/test_fpk_cmds.py` 依赖
  `os.symlink`、`test_fpk_pack.py` 依赖 `build.sh`（需要 rsync），二者在无特权的
  Windows 上无法运行。

## [2.7.3] - 2026-10-09

### 修复

- **发现页歌单卡名字退化成「QQ音乐推荐歌单」「网易云音乐推荐歌单」**：歌单卡的**真实名字**
  此前只存在于进程内的 `_DISCOVER_CACHE`（30 分钟 TTL、扩展重启即空、上游弱网/限流时整块
  为空），而反查用的 `_discover_detail_record()` 里是**硬编码**的 `f"{label}推荐歌单"`。
  于是只要 App 不是靠 `playlist/list` 里的卡片、而是按 guid 去 `playlist/detail` /
  `batch-detail` 反查（或扩展重启后再看），名字就只剩平台通用名。修复：
  ① 新增落盘索引 `recommend_cache/discover-cards.json`（`_DISCOVER_CARD_INDEX`），卡片抓到
  时记下真名 / 封面 / 作者 / 曲目数 / 播放量 / 简介（原子写、损坏静默降级、超 2000 条淘汰最旧）；
  ② `_discover_detail_record()` 优先用抓到的真名，**只有该歌单卡从未成功抓到过**时才退回平台通用名；
  ③ `_fetch_discover_cards()` 在上游不可用时回落落盘索引，避免歌单列表整块消失（只剩通用名条目）。
- **转码声明分片数自愈漏掉「重启后播放」这条路径（2.7.2 修复的漏网，导致仍旧整首跳下一首）**：
  自愈原先只挂在 `ensure_session()` 的复用分支上，但 App 重启后拉
  `/music/api/v1/track/hls/{guid}/preset.m3u8` 走的是
  `_transcode_live()` → `peek_cached()`，**完全绕过** `ensure_session()`。磁盘上 2.7.1 期间
  用估算时长落下的坏 `state.json` 因此照样输出比实际多的声明分片数 → App 拉不存在的分片
  404 → 整首跳下一首。修复：把自愈移到所有读路径的统一出口 `_usable_cache()`
  （`ensure_session()` / `peek_cached()` 都经过它），并保持自愈是纯函数、不回写磁盘
  （`state.json` 的 mtime 是配额 LRU 的访问时间，回写会把 LRU 顺序搅乱）。

### 新增

- **取流失败留痕**：`track/stream` 的在线取流此前失败是**完全静默**的 —— App 拿到 404 就跳
  下一首，而服务端一行日志都没有，导致「能显示曲目和歌名、一播就跳」在日志里不可见。
  现在两处都会告警：`resolve_lx_url()` 整条音质链失败（带音质链与最后状态，提示检查洛雪源
  是否已激活），以及 `stream_track()` 候选全部不可用时。
- **只读诊断脚本 `scripts/diagnose_playback.sh`**：`sudo bash scripts/diagnose_playback.sh`
  一把收集 播放问题所需的全部现场（容器状态、关键环境变量、洛雪 `/api/v1/source` 源激活
  状态、直链三档音质解析实测、取流相关日志筛选、转码 HLS 会话「声明数 vs 磁盘实际数」一致性、
  ffmpeg 负载、磁盘空间），输出到 `/tmp/fnmusic-playback-diag-<时间戳>.txt`，不改动任何状态。

### 开发

- `proxy/tests/test_transcode_compat.py` 新增 3 例：`_usable_cache()` 出口即自愈（且不回写磁盘）、
  `peek_cached()` 同样拿到自愈后的声明数、以及**端到端**用例「磁盘上留着坏会话时，重启后
  第一次拉 playlist 的声明分片数必须等于磁盘实际分片数」。经反向验证：关掉自愈即还原为多声明。
- `proxy/tests/test_discover_playlists.py` 新增 5 例：详情/批量详情端点回真实歌单名、重启
  （清空全部内存态）后仍回真名、上游返回空时回落落盘卡片、从未抓到过卡片时才用通用名。

## [2.7.2] - 2026-10-09

### 修复

- **推荐歌单曲目播放中断、整首跳下一首（2.7.1 引入的回归，严重）**：2.7.1 的在线曲目
  元数据登记表把**歌单标注时长**一并回填进了 `duration_s`，而这个值会流入「标准音质」
  的服务端转码链路：`transcode` 用 `floor(duration_s / hls_time)` 预合成完整的 VOD
  playlist（App 只拉一次 playlist，必须一次给全），其正确性依赖
  「声明分片数 ≤ ffmpeg 实际产出数」这一不变量 —— 见 `transcode.py` 开头
  「App 多要一片会 404」。该不变量只在 `duration_s` 来自**真实时长**时成立：
  上游拿不到时长时它是 `0`，`transcode.ensure_session()` 会用 `probe_duration()`
  ffprobe 实测；而歌单标注的是「上游平台官方版」时长，与实际播放的**洛雪音源直链**
  可能不一致，于是两个方向都会坏：
  · **声明数 > 实际数** → App 拉到不存在的分片 → 404 → 整首跳下一首；
  · **声明数 < 实际数** → playlist 提前 `#EXT-X-ENDLIST` → 后半段音频被丢掉、
    进度条走不满就跳下一首。
  修复两层：
  ① 登记表**只回填展示字段**（title / artist / album / cover_url），`duration_s`
  一律不动，让转码重新走实测时长 —— 即恢复到 2.7.0 的播放行为（`apply()` 内已写明
  理由，并加了专门的回归用例）；
  ② 新增 `transcode._heal_declared_count()`：复用磁盘上**已完成**的转码会话前，
  把声明分片数同步为磁盘上真实的分片数（双向，并同步 `duration_s`）。这一层是必需的
  —— 磁盘 state 是持久化的，只修上游调用点救不回 2.7.1 期间已经落盘的坏会话；
  顺带也消除了原先取 `floor` 带来的末尾截断（实测每首会少 0～10 秒尾巴）。自愈只在
  播放路径 `ensure_session()` 触发，配额扫描不受影响。

### 开发

- **转码声明分片数自愈测试**：`proxy/tests/test_transcode_compat.py` 新增 5 例，覆盖
  双向同步、声明与实际一致时不改动、磁盘无分片时不误改、`_count_segments()` 只认
  `^\d{5}\.m4s$`（忽略 `.tmp` 等），以及 `ensure_session()` 复用时会话的声明数已自愈。

## [2.7.1] - 2026-10-09

### 新增

- **平台推荐歌单（发现·歌单广场）**：把各平台的推荐歌单注入到飞牛音乐 App 的歌单列表，
  能力与柠檬音乐「发现」页同源（QQ 歌单广场 `/musicu.fcg get_playlist_by_tag`、网易
  `playlist/list`、酷我 `getRcmPlayList`、酷狗 `getSpecial`、咪咕歌单广场
  `playlist-square-recommend`）。每个平台按 `FNMUSIC_RECOMMEND_PLAYLIST_PAGE_SIZE`
  注入若干张虚拟歌单卡（`online:playlist:discover:<src>:<plid>`），点进去拉该平台歌单曲目，
  曲目按洛雪 `lx:<platform>:<id>` 契约下发，播放/歌词/收藏全部复用既有在线链路。
  属公共内容（不读个人历史与收藏），shared 会话同样可见；平台接口不可用时该平台静默跳过，
  不影响其他平台与官方歌单。开关 `FNMUSIC_RECOMMEND_PLAYLIST`（默认关）、
  白名单 `FNMUSIC_RECOMMEND_PLAYLIST_SOURCE`（空=全部）均支持热重载，WebUI「播放与推荐」
  页可配置。

### 修复

- **文件行尾（CRLF）污染安装脚本与构建参数，导致安装失败（严重，连续三轮失败的主因）**：
  仓库在 Windows 上以 `core.autocrlf=true` 检出后工作区文本文件全部变成 CRLF。
  第一层症状是安装脚本直接起不来 —— shebang 变成 `#!/usr/bin/env bash\r`，
  执行 `install.sh` 报 `line 2: set: pipefail: invalid option name`。
  修掉脚本自身后仍持续失败，真正根因是**无扩展名文件
  `container/lxserver-artifact/lxserver.version` 被「扩展名白名单」漏判**、未做 LF
  规范化：其尾部 `\r` 被 `. <file>` source 进 `LXSERVER_ZIP_NAME`，拼进下载 URL 后
  curl 直接报 `curl: (3) URL rejected: Malformed input`；终端回车符还会把 URL 显示成
  「缺 zip 文件名」的假象（`.../download/v2.1.2`），一度把排查方向带偏。修复分三层：
  ① 打包期「判文本」的规则由扩展名白名单升级为**白名单 + 内容探测兜底**
  （有 NUL 字节判二进制，否则尝试 UTF-8 解码，与 Git 同款启发式），并对所有判定为
  文本的文件做 LF 规范化（规范化文件数 71 → 72，多出的正是 `lxserver.version`）；
  ② `verify_eol_clean()` 打包门禁原先复用**同一个** `_is_text_file()` 且不传文件内容，
  漏判口径与打包期完全一致 → 门禁「假安全」。现改为先读取内容再判定，与打包期严格同源；
  ③ 新增 `.gitattributes`（`* text=auto eol=lf`，二进制类型显式 `binary`）从源头阻断
  autocrlf 转换，并新增 `scripts/check_eol.py` 接入 CI 两道门禁。
- **lxserver 引入步骤去除转义引号表达（构建加固）**：Dockerfile 原先用 `\"\"` 作为
  「官方源」占位符表达下载回退链，在 compose 变量展开 / BuildKit 引号处理下会被破坏
  （实测 `_chain` 展开为 `""" https://...`）。它并非上述安装失败的最终根因，但属真实
  隐患，已改为**不使用任何引号技巧**：独立 `try_url()` 函数 + 空元素过滤表达回退链，
  官方源单独放到最后重试。
- **推荐歌单/账号歌单曲目点开播放显示「未知曲目」「未知歌手」**：发现页各平台推荐歌单、
  网易账号歌单、每日/热门推荐下发的曲目**从未走过搜索**，因此 `proxy/app.py` 的
  `_online_info()` 第一步 `_retained_track()`（只查 `_SEARCH_CACHE`）必然落空；第二步转发到
  lxmusic-service `/api/v1/track/info` 时，该接口只对「自己搜索过的」曲目有缓存，未命中即
  用 `lxserver_client.synthesize_song_info()` 合成占位值 —— 即用户看到的
  `未知曲目` / `未知歌手`，是上游兜底常量，**不是飞牛客户端的 bug**。
  新增 `proxy/online_registry.py`：在**下发曲目列表时**把真实元数据登记进内存表并节流落盘
  （`recommend_cache/online-track-index.json`，14 天 TTL，8000 条上限，超出按最久未访问淘汰），
  `_online_info()` 的**全部 return 路径**统一过一遍 `REGISTRY.apply()`，用登记值替换占位值 ——
  仅在标题/歌手/专辑/时长/封面为占位或空时回填，**绝不覆盖上游真实数据**，因此搜索结果等
  既有链路行为完全不变。登记点三处：发现页平台歌单、网易账号歌单、每日/热门推荐。
- **compose 空值 build arg 覆盖 Dockerfile 默认值**：`docker-compose.yml` 曾以
  `PIP_FALLBACK_INDEXES: ${FNMUSIC_PIP_FALLBACK_INDEXES:-}` 形式透传备用源链，空值会
  以**空串**覆盖 Dockerfile 内 `ARG` 的默认值（并破坏 ARG 里的引号）。已从 compose
  移除这两项透传，改由构建层 shell 兜底（`${VAR:-内置默认}`），并在 Dockerfile 中
  把 ARG 默认值改为不带引号。
- **离线 lxserver 包随 fpk 分发**：`container/lxserver-artifact/*.zip` 原先被目录内
  `.gitignore`（`*.zip`）排除，仓库中只有 `.placeholder`，导致 Docker 构建时必然
  需要联网下载（国内弱网/被墙时必失败）。现已将校验过的 zip 放入构建上下文并随
  fpk 内层归档分发，构建时**优先使用预置包、零网络请求**；仅当预置包缺失时才走
  镜像加速回退链。
- **安装期 Docker 构建弱网加固**：apt/pip 源由「主源 → 官方源」二段回退改为**多级源链**
  （主源失败后依次重试多个国内备用源，官方源沉底兜底），并显式收紧 pip 超时/重试
  （`--timeout 30 --retries 2`），避免弱网下单源耗满默认 15s×5 把安装卡死。
  apt 的源替换改为按 `URIs:`/`deb` 行整行重写（原先按域名特征匹配，切到第二个镜像后
  会失配导致后续切换静默失效）。全部源链失败时输出明确排查指引。

### 开发

- **安装失败诊断脚本**：新增 `scripts/diagnose_install.sh`（只读）。采集安装日志、
  Docker 版本/daemon.json、宿主机 DNS 与关键域名解析、宿主机与容器内网络连通性、
  现有镜像/容器与磁盘占用，生成一份可直接回帖的报告，用于定位构建卡住的具体步骤。
- **行尾门禁脚本**：新增 `scripts/check_eol.py`。检查仓库工作区与打包上下文里所有
  「文本」文件是否为 LF（判文本口径与打包脚本同源），`--fpk X.fpk` 可额外解开 fpk
  外层归档与内层 `app.tgz` 逐文件校验；已接入 CI 的两道门禁，同时在
  `packaging/fpk/build_win.py` 打包末尾强制自检，产物不合规直接失败而不是产出坏包。
- **在线曲目元数据登记表测试**：新增 `proxy/tests/test_online_registry.py`（16 例），
  覆盖元数据提取（本地 guid 拒绝、三个显示字段全占位拒绝）、存取往返、批量登记过滤、
  `apply()` 回填语义、**不覆盖上游真实值**、无登记时透传、持久化与重载、TTL 过期、
  索引损坏降级，以及端到端回归「歌单曲目 → `/music/api/v1/track/metadata` 必须给出
  真实标题/歌手而非 `未知曲目`」。
- **Windows 原生 fpk 打包脚本**：新增 `packaging/fpk/build_win.py`，在没有
  rsync / WSL / Docker / fnpack 的 Windows 环境下也能产出结构等价的
  `dist/fnmusic-ext-<版本>.fpk`（含 `.sha256`）。产物契约与 `build.sh`+fnpack 一致：
  内层 `app.tgz` 条目带 `./` 前缀、外层条目无前导 `./` 且目录在前、`manifest.checksum`
  = app.tgz 的 md5、脚本权限位取自 git index、载荷排除表与 `build.sh` 的 rsync 规则对齐；
  同样会内嵌 `packaging/fpk/.build/cache/` 中的 lxserver 预编译包以支持 NAS 离线安装。

## [2.7.0] - 2026-10-09

自 2.6.3 以来的全部变更收拢为本次正式版发布（涵盖 2.7.0a、2.7.0b、2.7.0c 迭代改进）。

### 新增

- **洛雪音源底层全面接入官方生态实现 lxserver（搜索/解析/播放/歌词全面升级）**：
  1. **洛雪桌面版原生 musicSdk 支持**：采用成熟的 XCQ0607/lxserver 服务端（内嵌洛雪桌面版官方 musicSdk 移植）替换原有自研 Python 抓取器与 Node 沙箱，完整覆盖酷我(kw)、酷狗(kg)、QQ音乐(tx)、网易云(wy)、咪咕(mg)五大平台的免登录搜索、榜单、歌词与元数据解析；
  2. **支持洛雪自定义音源规范**：原生兼容 `globalThis.lx` API 全家桶及桌面版自定义源脚本（vm2 沙箱隔离 + 多源调度与自动重试）；
  3. **契约保持与防护机制完善**：适配层保持 `lx:<src>:<id>` ID 格式与端点契约不变，无缝兼容老用户收藏、歌单与最近播放；继承 Range 魔数流式探活、音质阶梯降级、搜索门闸防抖与链路熔断机制；
  4. **版本解耦与离线打包**：新增 `container/lxserver.version` 版本锁定机制，fpk 打包内嵌预编译产物，NAS 终端安装 100% 离线；Dockerfile 内置四层镜像回退链；
  5. **历史数据平滑升级迁移**：容器启动时自动检测并迁移旧 `/data/lxmusic` 上传的脚本至新版目录。
- **洛雪自定义源多源管理**：管理台支持维护多个洛雪源脚本（在线 URL 与本地上传的 `.js` 文件）及备注名；单源互斥激活，支持一键切换并即刻生效。
- **搜索深分页（本地优先布局 + 在线结果按需取页）**：官方客户端搜索翻页时，本地曲库结果始终占据前列，在线结果智能填充余量与后续分页，支持预取零等待、取尽判定与页数上限（`FNMUSIC_SEARCH_DEEP_MAX_PAGES`，默认 10）。
- **搜索结果在线来源标记（仅展示）**：搜索结果在线条目在歌名前加来源标记：网易音乐盒子 → `[music box]`、musicdl → `[dl]`、洛雪 → 备注名或 `[lx]`；播放、收藏、加歌单、边听边存等业务逻辑及入库标题保持纯净。
- **卸载默认保留音乐源数据**：应用中心卸载扩展时，默认将所有音乐源数据与配置保留在存储卷持久目录；重新安装时自动检测并无缝恢复历史数据。

### 修复

- **本地歌曲封面直读官方封面目录**：探测飞牛官方封面根目录，本地歌曲封面直接按魔数嗅探返回，支持客户端 `size` 优先缩略图，彻底解决接管后本地歌曲封面异常回退占位图的问题。
- **洛雪自定义多音乐源保存与校验修复**：
  1. 修复在线 JS URL 校验时缺少 `time` 模块导入导致的异常；
  2. 修复本地 JS 上传至 lxserver 端点的数据格式并按解析生成的真实源 ID 绑定持久路径；
  3. 修复同一脚本重复上传触发 500 冲突问题，优化为自动复用已存在的源；
  4. 修复新导入/未激活源因处于禁用状态导致抽样解析探活被拒绝的问题；
  5. 修复激活保存时根据 URL 派生源 ID 进行精确激活。
- **洛雪多音源架构、深分页稳定性与 WebUI 管理健壮性增强**：
  1. **Supervisord 全生命周期与 CI 稳定性**：容器 5 个受管服务纳入统一进程断言，补齐 CI 自动化构建与测试依赖，强化测试执行目录沙箱隔离；
  2. **卸载/重装数据持久化防护**：修复 `install.sh` 与 `uninstall_init` 数据恢复校验逻辑，杜绝覆盖失败导致的历史数据丢失；
  3. **洛雪单源原子切换与并发互斥**：引入 `asyncio.Lock` 互斥保护，激活目标源前增加存在性探针以防误关现有音源，并在校验探活时通过 `excludeApiSources` 隔离其他激活源干扰；
  4. **lxserver 上游契约与歌词解析增强**：平铺点分隔配置项，修补上游 Dockerfile 解包歌词 Promise 未等待缺陷并增加 GET/POST 双重退避回退；
  5. **深分页游标隔离与客户端驱动**：为网易与洛雪深分页维护独立请求游标与源维度查重集合，打破跨源重歌误判取尽与循环死锁，TTL 重查时安全重置深分页状态；
  6. **WebUI 保存重试与预览清理**：支持在 `.env` 无差异时重新触发音源激活，多进程预览启动失败提供严格逆序回滚清理。

## [2.6.3] - 2026-09-30

### 修复

- **第三方音源（墨澜/星海等）搜索超时与无结果修复（新增逐曲探活可选控制）**：
  1. **搜索性能优化（毫秒级直出）**：解决第三方音源在搜索阶段因并发解析直链与网络探活导致 12s 超时（`search deadline exceeded`）以及频繁触发熔断导致全平台搜不出歌曲的问题。新增 `逐曲探活(beta)` 开关（默认关闭），关闭时搜索候选曲目跳过直链解析与 Range 探活，单次搜索耗时从 12 秒降至 30 毫秒，并在播放时按需解析真实音频流；
  2. **代理层放行收费曲目**：逐曲探活关闭时，代理层对来自第三方音源的搜索候选曲目豁免 VIP/收费元数据拦截（同时严格保留试听、坏链接与无音频流等真不可播防御），使 VIP/版权曲目能够正常展示并在点播时交由用户源解析；
  3. **WebUI 体验优化**：新增「搜索」独立菜单页（置于播放与推荐下方），移入搜索超时设置并提供逐曲探活开关及作用说明；清理「音乐源」页面过期的切换描述文案。
- **转码下载文件接口报 500（`UnicodeEncodeError`）**：下载曲库歌曲或透传官方转码任务时，官方后端在 `Content-Disposition` 响应头中携带原始非 ASCII（中文）文件名；代理拦截层依赖的 Starlette 框架在处理 HTTP 响应头时无条件使用 Latin-1 编码，触发 `UnicodeEncodeError` 导致代理对外直接报错 `500;http error`。现增加响应头安全规范化处理（`_sanitize_content_disposition`），将中文文件名规范化为标准 RFC 6266/5987 格式（ASCII 安全回退 + `filename*=UTF-8''`），并确保代理自发文件交付时同样附带标准格式，彻底杜绝响应头非 Latin-1 导致的 500 异常。
- **音源无损档交付损坏数据导致"转码下载失败"**：飞牛音乐 App 在转码或下载时，若曲库中对应歌曲文件损坏，官方后端解码器会报 `errno 68157441 transcoding error` 导致下载进度卡在 0% 或直接失败。经排查，部分音源（如酷我官方 CDN）无损档（FLAC）近期交付了文件头正常但正文含坏帧的不可解码数据，此前代理只校验 HTTP 状态码与文件大小便写入曲库，导致官方转码器无法处理。现建立三层防护机制：
  1. **音源服务头探针与自动降级**：音源服务在输出无损音频直链时，取前 3MB 数据流做 ffmpeg 解码探针，探针失败判定为损坏流，自动走官方接口强制降级为干净的 MP3（320kbps）音频流。
  2. **代理入库前全量解码校验与回退**：代理后台完整抓取（`_full_fetch_download`）或边听边存（`_tee_finalize`）无损文件落盘前，使用 ffmpeg 进行全量解码校验，校验失败立即拦截入库并自动重试拉取 MP3 档位，确保曲库中绝不写入坏文件。
  3. **坏流曲目拉黑机制**：被判定为损坏的在线曲目加入内存黑名单（6 小时有效），后续直接使用 MP3 档位请求，避免死循环。
- **App 音质偏好选"标准"后在线歌曲无法播放**：飞牛音乐 App 把音质偏好设为"标准"后，播放器不再直拉歌曲原文件，改向服务端要转码后的低码率 HLS 流（fMP4/AAC 分片）；代理此前只回一个"假"的转码应答——播放列表里唯一的分片仍指向原始大文件，既不省流量，音源关闭（如只启用 musicdl 时播网易歌曲）还会直接 404 跳歌。现由宿主机 ffmpeg 把在线曲目实时转成真正的 AAC/fMP4 分片流（默认 128kbps、10 秒/片，可经 `FNMUSIC_TRANSCODE_*` 环境变量调整、改 `.env` 热生效），同一首歌转码完成后缓存复用（配额 512MB、最久未用先清），并支持 App 的心跳保活与停止转码；边听边存照常在后台保存原文件。ffmpeg 不可用或音源临时失效时自动回落旧行为，不影响播放。
- **App 音质偏好选"标准"后下载失败**：App 的下载同样走转码协议（先建任务、轮询进度、再取文件），官方后端不认识代理的在线歌曲 id，请求透传官方必然失败。现代理对在线歌曲按官方协议的实测形状仿真（任务号 `downloadId`、状态 `waiting/transcoding/ready`、进度 `percent`）：标准档交付 MP3 320kbps 转码文件（与官方"标准"档一致；源已是 MP3 时直接供原文件不做有损重编码），原始档直接交付已下载的原文件，均支持断点续传；下载本地歌曲、或任何代理不认识的下载任务，一律原样透传官方，行为零变化。
- **已下载的在线歌曲偶发重新出网**：边听边存完成后会在缓存里记一条指向曲库文件的引用，个别情况下该引用丢失，再次播放同一首歌时会重新从源站下载、曲库出现重复文件。现引用写入后立即回读校验；即便引用丢失，也会按"歌名+歌手（+专辑）"在飞牛曲库反查并自动补回——音源已关闭也能直接播已落库的本地文件，不再出网、不产生重复文件。
- **偶发上游连接失败导致请求报错**：官方服务重启窗口内的陈旧连接会让紧接着的 GET/HEAD 透传瞬时失败（表现为封面、歌曲信息偶发加载出错）。现此类"还没换来任何响应"的连接级失败对幂等请求自动重试一次；已经开始输出内容的请求不重试，避免重复扣减。

### 变更

- **`FNMUSIC_TRACE_FORWARD` 抓包日志恢复可见**：开启 `trace_forward` 后子进程会打印 `download/*` 双向报文（`[dl-capture]` 前缀），用于 App 下载协议对齐取证；但守护层（takeover）的日志白名单未放行该前缀，日志实际到不了系统日志。现白名单放行 `[dl-capture]` 固定格式（限 600 可打印字符），远程排查 App 下载问题时抓包日志可正常留存。

### 安全

- **数据目录与配置备份权限收紧**：存放网易登录凭据的 `sources-data` 数据目录此前安装/升级后被统一放开为 0755，本机其他用户可直接读取网易云音乐登录 Cookie；现安装与升级统一将其收紧为仅运行账户可访问（目录 0700、文件 0600，属主对齐容器运行账户，不影响容器读写）。管理页保存配置时生成的 `.env.webui.bak` 备份（含 API Key）此前按默认权限 0644 落盘，现与 `.env` 本体一致收紧为 0600。

## [2.6.2] - 2026-09-29

### 修复

- **"最近播放"在线歌曲无法移除/编辑**：在"最近播放"里长按移除、或"编辑"模式多选移除在线歌曲（NAS 曲库中没有文件的）时提示"移除最近播放失败"，条目刷新后依旧存在。官方前端删除最近播放走批量字段 `trackGUIDs`（数组，单条移除与多选移除共用），代理的删除接口此前只认单数 `trackGUID`/`guid`，解析为空后把请求整包转发官方后端——在线歌曲在官方曲库里没有记录，官方必然拒绝，代理本地存储的在线播放记录一条都没删掉。现按官方契约解析 `trackGUIDs` 数组（兼容旧单数字段与 DELETE 方法）：纯在线批次直接删本地记录；在线与本地歌曲混选移除时本地部分先转发官方、成功后再删在线条目，官方失败则透传错误、本地不误删。
- **搜索偶发零结果（歌曲/歌手搜不到）**：连续搜索或紧接着重搜时，上一个关键词的搜索任务还占着音源的单线程通道（库层搜索 + 每首歌逐条串行探活直链，全程可达十几秒），新搜索对酷我/咪咕直接拿到"源忙"、25 毫秒就交回 0 条，代理把这当成"搜索完成"立即响应——而真正的结果几秒后才在容器里搜出来，无人接收；且带错误的结果不落缓存，每次重搜都重复同样的竞态。现音源忙时在自身超时预算内排队等通道释放后照常执行；直链探活改为共享线程池并行探测、凑够条数即停，单次搜索占坑时间大幅缩短；代理侧对"快速交回的 0 条 + 错误"追加一次延迟重试兜底。搜索结果交付顺序、超时部分结果（partial）语义与之前一致。

## [2.6.1] - 2026-09-29

### 修复

- **musicdl 平台选择保存后不再弹回默认（#31）**：管理页保存平台白名单后 `.env` 已正确写入，但 `supervisorctl restart` 拉起的 musicdl 进程继承的是容器启动时刻的环境变量，拿不到新值，重开页面平台又显示默认的酷我 + 咪咕。现 musicdl 进程每次启动直接解析 `/repo/.env` 的 `MUSICDL_SOURCES`（`.env` > 环境变量 > 内置默认），WebUI 保存 → 重启进程 → 白名单即时一致，容器重启后同样保持。附带修复 WebUI 写 `.env` 前的备份文件名（此前会生成 `.env.env.webui.bak` 畸形名）。

## [2.6.0] - 2026-09-28

自 2.5.0 以来的变更收拢为本次正式版。2.6.0a–2.6.0e 仅为测试构建，未单独发版。

### 修复

- **搜索分页跨页重复**：飞牛官方 2026-09-25 更新后搜索接口忽略 `size` 参数全量返回结果集（`total=11`、`size=10` 时第 1/2 页返回同样 11 条），代理此前只透传官方页内容，导致本地歌曲在每个分页整段重复出现（第 2 页起每页都带全部 11 首本地结果）。现检测到"返回条数超过单页窗口"时按请求窗口对本地段切片：第 1 页恰好 `size` 条、第 2 页为余量本地歌曲接在线结果，跨页零重复、`total` 稳定，官方正常分页时行为不变。
- **网易账号歌单点开能看到歌曲**：账号歌单与热门推荐一样标为虚拟歌单，曲目列表按每日推荐补齐时间与歌手/专辑字段后再下发。
- **推荐歌单落盘不再变成 unknown**：每日推荐、热门推荐和网易账号歌单的曲目不进搜索缓存。边听边存、后台整轨下载和断点续传落盘时，按曲目 id 回查这些歌单缓存，再回查播放历史、收藏和自建歌单；本地仍没有标题时，后台下载再向源站要一次歌曲信息。文件名用歌手和歌名，不再写成 `unknown (n)`。
- **专辑标签不再写入对象文本**：前端曲目的专辑是 `{name, guid, ...}`。落盘、写音频标签和播放历史上报都先取出专辑名、歌名和歌手的纯字符串，已有的专辑名不会被另一份缓存覆盖。

### 新增

- **网易账号歌单（`FNMUSIC_NETEASE_MY_PLAYLISTS`，默认关闭，热重载）**：「网易云音乐盒子」音源启用并扫码登录后，网易账号里**自己创建**的歌单（含"我喜欢的音乐"）以只读歌单出现在音乐页——「热门推荐」下方、官方歌单上方，点开即听；收藏的他人歌单不显示。曲目经可播过滤（VIP/试听片段自动剔除），播放、取链、歌词与封面沿用既有网易链路。歌单为只读：在音乐页往里加歌、移歌或删除均不回写网易（请求被静默吸收，不报错）。开关在管理台「音乐源」页「搜索超时时间」下方。摘要 5 分钟缓存、拉取失败 2 分钟冷却并回落 24 小时磁盘缓存（musicbox 重启窗口不塌列表）；列表注入后串行后台预取各歌单曲目，大歌单首开不等待批量详情；代理重启后从磁盘缓存恢复伪装 id 反查，旧会话卡片仍可打开。歌单上限 100 个、每歌单曲目上限 1000 首。
- **自动下载歌词（`FNMUSIC_LYRIC_AUTO_DL`，默认关闭，热重载）**：边听边存与「收藏自动绑定本地」后台下载的歌曲在完整落库后，自动下载同名 `.lrc` 歌词文件放到歌曲同一文件夹，官方 App 扫描入库后播放即显示歌词；歌曲下载成功后才去下歌词，不产生"只有歌词没有歌"的残留文件。酷我源歌词接口已失效，该源暂无歌词。开关在管理台「边听边存」页。
- **自动下载封面（`FNMUSIC_AUTO_COVER`，默认开启，热重载）**：边听边存与「收藏自动绑定本地」后台下载的歌曲在完整落库后，自动从源站下载封面并内嵌进音频文件（MP3 APIC / FLAC Picture / MP4 covr / OGG），官方音乐 App 里下载的歌曲即显示封面图。封面在歌曲保存成功后才处理、直接嵌进音频文件本身——不产生独立封面文件，不存在"歌曲下载失败、封面文件残留"的垃圾文件；源站文件自带内嵌封面时自动跳过不重复下载。已实证飞牛官方扫描器只读音频内嵌标签（不支持 cover.jpg 等外置封面文件），内嵌是官方 App 显示封面的唯一途径。

### 音源

- **musicdl 跟随上游升级到 2.14.0**：带入酷狗歌词获取修复与 QQ 第三方接口更新。平台注册表仍为 57 个，编号表不变。网易云音乐盒子维持 0.5.3。

### 安全

- **管理页使用飞牛管理员身份**：在已登录的飞牛管理员页面打开，安装不再询问或保存单独密码。管理接口只接受网关注入的管理员身份。8774 只监听本机。
- **音源端口只留在本机**：musicbox 宿主机发布为 `127.0.0.1:8770`。网易扫码走已登录管理页里的反代。
- **洛雪源来源提醒**：导入 URL 或 `.js` 前必须自行确认来源安全，不要导入来历不明的脚本。脚本在容器内执行。

## [2.5.0] - 2026-09-28

自 2.3.0 以来的全部变更收拢为本次正式版发布（期间的 2.4.x / 2.5.x 迭代版本仅作为测试构建分发，未正式发版）。

### 兼容适配（官方 2026-09-25 更新）

- **适配飞牛官方音乐 2026-09-25 更新**（SQLite 升级 v4、底层接入 bleve 全文检索）：新增未拦截 `/music/api/` 端点探测与采样日志（`FNMUSIC_TRACE_FORWARD=detail`），未拦截新端点可靠透传，官方后续更新可即时审计。
- **边听边存不再误删刚完成的音频（#28）**：客户端在下载结束瞬间快速断开时，已拉取完成的音频临时文件此前会被清理逻辑误删；现落盘、写标签与曲库重扫通知全程护航执行。
- **彻底避免 Unknown 文件落盘（#28）**：首次解析慢产生的残缺元数据不再永久占位，完整信息随时覆盖；落盘缺元数据时自动从在线播放历史/收藏快照回查命名。另附存量修复脚本 `scripts/repair_unknown_library.py`（默认 dry-run 预览）。

### 收藏与歌单

- **「收藏自动绑定本地」开关（默认关闭）**：收藏在线歌曲或加入自建歌单先立即生效，后台自动下载到本地曲库并绑定，换源或原音源下线后依然可以播放。
- **下载完成后自动写入官方收藏/歌单**：官方曲库收录文件后自动把这次收藏/加歌单写进官方列表，同一首歌不再"官方 + 本地映射"重复显示两次；官方未收录前保留本地映射兜底，收藏永不丢失；代理重启、下载中断等场景自愈重试，取消收藏/移出歌单自动翻译转发官方接口。
- **边听边存切歌不中断下载**：切歌/退出时未写完的部分自动转后台任务断点续传（`Range` 从断点续拉），源不支持续传时回退整轨重下。

### 每日推荐

- **按账户隔离，各账户内容不同**：引入"音源名额"机制——每天第一个打开每日推荐的用户使用网易日推，其余账户由大模型按各自播放历史与收藏生成，或从本地曲库随机补齐（按各账户"最近没听过"挑选），三层兜底保证推荐永不为空。
- **逐首可播校验与渐进式上架（#23/#29）**：候选歌曲入选前实时探活，失效条目不入列；推荐增量流式生成，有 1 首即刻展示、后续重进递增，不再整块等待；大模型层超时不再拖垮兜底层导致空列表。
- **切源立即失效当日推荐缓存（#22）**；音源全挂导致空结果后 5 分钟冷却，不再每次打开列表都空转重试。

### 在线专辑（#22）

- **专辑搜索多源支持**：拦截官方专辑搜索端点，网易云走真实专辑检索，musicdl 与洛雪源按「专辑名 + 源」自动聚合合成，与官方本地专辑无缝合并返回。
- **专辑详情页不再空白**：合成专辑曲目列表接口（支持分页与音频规格注入）；搜索注入的在线专辑锚点落盘持久化，代理重启后仍可正常进入详情。

### 洛雪自定义源

- **沙箱 CPU 治理（#29）**：第三方脚本的短周期定时器强制钳制至 500ms、数量与触发频率设限，根治部分脚本死循环空转导致 Node.js 常驻进程空烧 50% CPU；服务异常终止遗留的孤儿 bridge 进程启动时定向清扫。
- **社区脚本集兼容性（pdone/lx-music-source 全矩阵实测）**：脚本初始化超时 10s→30s，"慢而活"的服务端中转型脚本不再被掐死在激活环节；网络错误透传底层原因（如 `fetch failed (EHOSTUNREACH)`）；「测试源」失败新增 `server_unreachable` 归类，明确说明"上游 API 服务器已停摆、换源即可"，不再误导向本扩展的兼容性问题。
- **源校验按平台细分（#22）**：不再因单一平台搜索波动误判整源；酷狗搜索时长单位修正（此前时长全部显示为 0:00）。

### 安装与升级

- **安装网络加速与进度可见（#24/#27）**：基础镜像拉取并发测速择优、长耗时阶段 30 秒心跳日志，进度透明不卡死；新增 `scripts/collect_support_info.sh` 一键采集无敏环境信息，便于 issue 反馈。
- **应用中心安装/升级健壮性**：FPK 回调自动接管（`--adopt`）宿主机老部署，Git 手工部署/旧卷迁移不再报冲突死锁；首次安装自动平滑继承老部署的收藏、播放历史与音源凭据；卸载彻底清理全局部署记录。
- **升级/切源后「等待 musicdl healthz 超时」根因修复**：`.env` 换源重装时容器仍跑旧进程集导致新音源端口无人监听——现等待前自动对齐容器进程集；升级失败不再遗留孤儿容器；等待超时自动采集容器内进程状态与日志尾部随安装日志落盘，一眼可辨。
- **在线搜索"全源失败 0 条"不再污染缓存**：连续快速搜索（如回车瞬间歌曲与专辑搜索并发）时音源占线返回的空结果不再缓存 10 秒，同词重搜立即重新聚合。
- **`FNMUSIC_LIBRARY_SCAN_PATH` 补全文档**并在管理台「边听边存」页暴露为选填项（官方通常自动扫描入库，填入抓包路径可加速官方收录）。

## [2.3.0] - 2026-09-24

### 新增

- **在线曲目加入/移出自建歌单闭环**：拦截官方 `add-track` / `remove-track` 接口，在线曲目记入本地持久化存储（按用户/按歌单隔离），自建歌单曲目列表透传官方后自动合并下发在线条目（带收藏状态、分页翻页不重不漏），`playlist/detail` 与 `batch-detail` 歌单曲目总数实时并入在线曲目数；删除歌单时自动级联清理本地关联数据，代理重启后支持自愈重建伪装映射。
- **全链路 e2e 冒烟测试脚本 (`scripts/e2e_check.sh`)**：一键自动化覆盖代理接管、多源搜索、边界分页、在线取流播放、收藏增删联动、播放上报与历史、歌单增删歌曲闭环全链路。
- **曲库自动重扫通知钩子**：新增 `FNMUSIC_LIBRARY_SCAN_PATH` 环境变量，边听边存落盘后支持按凭证合并触发官方媒体库重新扫描（3 秒合并窗口 + 90 秒节流）。

### 修复

- **构建与安装镜像源加速**：针对阿里云镜像 CDN 在 HTTP/1.1 连接下单连接限速（~350KB/s）导致构建/安装耗时过长问题，默认 apt/pip 镜像源全面切换至腾讯云镜像（实测 20MB/s+），全新安装与无缓存构建时间从十几分钟大幅缩减至约 2 分钟；存量 `.env` 默认镜像自动平滑迁移。
- **搜索翻页跨页重复与截断修复**：官方搜索接口越界页回声导致多页重复拼接问题修复；全局起点越过官方段时正确丢弃官方回声；支持推荐歌单 `size=-1` 返回全量。
- **网易云多项稳定性与体验修复**：
  - 扫码登录态更新迟滞：扫码成功立即重建常驻客户端实例，增加登录态探测缓存失效与过期自动重读。
  - 点播取链超时优化：由每次起 CLI 子进程改为服务进程内直调（毫秒级响应并带 10 分钟短缓存），代理层解析超时放宽至 6 秒。
- **流媒体播放与边听边存稳定性**：
  - 在线流读取超时从 10 秒放宽至 60 秒，避免 CDN 限速抖动掐断流；流中断加入审计日志便于取证。
  - 曲目结束时的文件落盘与标签写入移入后台异步线程，避免阻塞代理其它请求。
  - 分段拉流（Range 请求）客户端边听边存失效修复：针对此类客户端在首次取流时由后台整轨异步拉取完整曲目落盘曲库。
  - 本地歌曲播放响应透传精确 `Content-Length`，消除分块传输带来的起播卡顿或播放器内核时长计算异常。
- **播放历史与数据维护**：拦截 `play-history/delete`，支持在线条目从本地播放历史彻底删除；官方条目正常转发。
- **安装、升级与部署边界鲁棒性**：
  - 完善代理 systemd unit 归属校验，旧路径失效或跨卷迁移时提供自动接管与自愈指引。
  - fpk 升级与卸载失败时降级兜底处理，确保应用管理不卡死。

## [2.2.8] - 2026-09-23

### 修复

- **安装时拉不到依赖：清华镜像源近期对部分网络返回 403**：pip 与 apt 的默认源
  从清华换成阿里云（`mirrors.aliyun.com`），安装与升级不再依赖单一镜像。
  宿主机 pip 的回退链同时调整为 阿里云 → 清华 → 官方 PyPI，一家不可用时自动换下
  一家重试。已装机用户升级时，配置里仍是历史默认清华源的会自动迁移到阿里云；
  手动改过源地址的不受影响。

## [2.2.7] - 2026-09-23

### 修复

- **搜索在音源回包前就交出空列表**：在线搜索只留一个超时，默认 15 秒。有在线结果立刻返回，本地歌曲在前；到时还没有就放弃这次还没回来的结果，只返回本地歌曲。管理页「音乐源」下方可改「搜索超时时间」，保存后立即生效。
- **搜索在停手 1 秒后才发给音源**：1 秒内继续输入就换成最新关键词并重新计时，窗口过去才真正搜索。
- **输入过程中搜索先空白、过一会儿再搜才有结果**：每个音源同时只搜一个关键词。同一用户继续输入时只保留最后那个词；洛雪会取消上一轮，musicdl 和网易等当前请求结束后丢掉旧结果再搜。被换掉的空结果不再缓存。
- **洛雪源 URL 测试显示 502**：桌面网关连接 WebUI 时把 5 秒建连超时留在了套接字上，校验要几十秒才返回，连接被中途拆掉，页面只看到 502。转发超时改为 180 秒。
- **电脑上传和 NAS 选择 .js 都失败**：管理页把脚本当 JSON `{filename, script}` 提交，接口却只接受 multipart，于是提示「请用 multipart/form-data 上传文件」。现两种格式都接受。

### 新增

- **搜索结果本地优先**：接管后官方搜索把本地曲库与在线音源结果按页交错混排，
  在线条目还会挡住后续页的本地结果。现改为全局统一布局——本地条目占满前
  `本地总数` 位、在线条目（网易 > musicdl > 洛雪）紧随其后，翻页顺序严格
  「先本地、后在线」，`total` = 本地 + 在线。
- **洛雪源支持文件方式配置（重要）**：管理页「音乐源 → 洛雪自定义源」新增三种
  配置方式——粘贴 `http(s)` URL（原有）、上传电脑上的 `.js` 文件（multipart →
  容器 `sources-data/lxmusic/uploads/` 落盘，持久化随数据卷）、从 NAS 选择
  `.js`（飞牛桌面内经开放 API `pickUserFile` 文件选择器选中，由宿主侧网关代读
  内容后同样落盘）。上传脚本以 `file:///data/lxmusic/uploads/<名字>.js` 形态
  参与统一的「测试 → 保存 → 激活」链路；manifest 声明 `micro_app=true` 并申请
  `trim.file.userAccess` scope。
- **`install.sh --lx-source-url` 支持本机路径**：传入宿主机上存在的 `.js` 文件
  路径时自动复制进 `sources-data/lxmusic/uploads/` 并转为 `file://` 挂载地址，
  校验/激活链路复用不变。

### 变更

- **安装向导不再索要洛雪源 URL**：fpk 向导移除 `wizard_lx_url` 输入框与
  `wizard_lx_skip_verify` 开关（平台向导无文件上传组件），选 lxmusic 直接完成
  安装（无源状态），源脚本装好后在管理页配置；CLI `--lx-source-url` 变为可选。
  升级流程兼容 `.env` 中 `LX_SOURCE_URL` 为空的部署。

## [2.2.6] - 2026-09-22

### 修复

- **飞牛桌面里的管理窗口一片空白（重要）**：桌面页面是 HTTPS，管理入口却把 `http://主机:8774` 嵌进 iframe，浏览器丢弃混合内容，窗口只有标题栏。改为走桌面网关的同源路径 `/app/fnmusic-ext/`，由本机 socket 转到 WebUI。直连 8774 仍然可用。
- **应用中心安装在启用扩展时静默失败（重要）**：`extend.sh` 用 `read` 重读 `VERSION`。该文件没有末尾换行时 `read` 返回 1，`set -e` 在打出任何日志前退出。安装脚本已经印完成功提示，弹窗却只剩一份即将被回滚删掉的日志路径。改为 `head` 读取。
- **安装/升级日志在回滚后仍保留**：过程输出追加到 `/var/log/apps/fnmusic-ext-install.log`（写不了则落到 `/tmp/fnmusic-ext-install.log`）。失败弹窗带上该路径；没有 `[ERROR]` 行时附上日志末尾。
- **musicdl 搜索连续几次都是 0 首歌**：一次搜索向每个平台要 60 首（6 页），平台还要逐首确认直链。等不及就记一次失败，下次等待被压到 3 秒，短到一页都回不来，界面就是空的；上一轮还占着平台时，新的搜索会立刻被拒绝。现改为每个平台只取一页（10 首），超时下限放到 8 秒，已经交出歌曲就不再把等待往下压。空结果只缓存 10 秒。

### 文档

- README 标明 Gitee / GitHub 仓库地址，并说明 musicdl 部分平台歌曲少或不可播放、洛雪源能否搜索和播放取决于所填 URL，需自行选用可靠源。

## [2.2.4] - 2026-09-22

### 修复

- **管理页在手机上的保存条与输入框**：保存条按底部导航的实际高度上移（含安全区），不再压住导航；表单输入拉满卡片宽度，长错误文案换行，手机聚焦输入框不再把页面放大。
- **洛雪源 QQ/酷狗歌曲信息缺字段**：搜索结果补上 QQ 的 `strMediaMid`（与 songmid 不是同一个值）和酷狗/QQ 的 `albumId`，再交给自定义源脚本。六音等源的酷狗解析和依赖媒体 mid 的 QQ 解析此前拿不到这些字段。
- **应用中心卸载备份路径**：存储卷路径解析失败时归档落到 `/vol1/`，避免拼成缺少斜杠的根目录文件名导致卸载备份失败。

- **WebUI 网易扫码登录"扫码后没有反应"（重要）**：musicbox CLI 的
  `auth login/check` 返回 `{ok, data:{code}}` 信封结构（与
  `netease_login.sh`、musicbox 服务契约一致），而 `static/app.js` 的
  `pollQr` 读的是顶层 `st.code`——恒为 `undefined`，801/802/803/800
  全部分支失配，界面永远停在"等待扫码…"。现改为 `st?.data?.code ?? st?.code`
  （信封优先、扁平兼容），并把轮询主体拆成 `checkQrStatus` 便于测试。
  同时补齐登录成功后的状态同步：803 后经 `/api/netease/auth/status`
  （带重试）取昵称写入 `#qr-check`（此前该元素从未被写入），生成二维码
  前也先同步一次当前账号态。新增 `test_app_js.js/.py`（node 原生
  assert + 最小 DOM/fetch 桩）锁信封解析行为，`test_webui.py` 反代
  mock 由失真的扁平结构改为真实信封并补上游异常用例。

## [2.2.3] - 2026-09-21

### 修复

- **应用中心安装失败弹窗误报 Dockerfile `fix_dns`（重要）**：BuildKit 把整段
  apt `RUN` 打成步骤标题，而脚本里有 `echo "[ERROR] ..."`，弹窗的
  `grep '[ERROR]'` 命中标题后 300 字截断，只剩 `fix_dns` / `getent hosts`，
  把后面真正的 installer `[ERROR]` 吞掉。现过滤 `#N [ n/m] RUN` 标题，
  Dockerfile 失败文案改为不带方括号的 `ERROR:`；`compose up --build` 失败
  时 install.sh 补一条短 `log_err`。
- **fpk 安装失败回滚留下孤儿容器**：应用中心删掉 target/repo 但不删
  `fnmusic-sources`，空挂载容器继续占名、healthcheck 报「未配置任何音源」。
  `install_callback` 失败时先 `docker rm -f`。
- **构建层 DNS 自愈不再依赖 getent**：始终把 223.5.5.5 / 119.29.29.29 写到
  resolv 最前（写入失败不中断），`find` 取 apt 索引加 `|| true` 避免
  `set -e` 打死；compose 构建使用 `network: host` 以便复用宿主 DNS。
- **fpk 升级备份在调用方目录展开 `.env.bak*` 会失败**：`fnmusic_data_items`
  改为在仓库目录内展开通配并返回真实相对路径，`tar -C repo` 不再吃到
  调用方 cwd 里的同名文件。

## [2.2.2] - 2026-09-21

### 修复

- **洛雪服务端中转源解析全挂——`lx.request` 缺 Promise 形式（重要）**：官方
  `lx.request` 不传 callback 时返回 Promise（resolve 整个响应对象），主流
  服务端中转源（如"独家音源"）全部用 `await lx.request(...)` 拿响应；沙箱桥
  只实现了回调形式并返回取消函数，这类源拿不到响应、只能抛自家通用错误
  （如 `unknow error`），导致安装向导的洛雪源全链路校验必失败、安装中止
  （应用中心弹窗只显示"详见日志"且日志随回滚被删）。现补齐双契约：无
  callback 时返回 Promise，有 callback 时保持 `(err, resp, body)` 回调与
  取消函数返回值。
- **应用中心安装/升级失败弹窗看不到真实原因**：install_callback /
  upgrade_callback 失败时把日志末尾的 `[ERROR]` 行（剥离 ANSI 色码）带进
  弹窗文案——此前安装失败回滚会删除 TRIM_PKGVAR 里的日志，用户无从排查。

### 新增

- **`--lx-skip-verify` 跳过洛雪源可用性校验**：安装时直接激活洛雪源、不做
  全链路校验（源状态装好后在管理页 WebUI 查看/重配）。fpk 安装向导新增
  "跳过洛雪源可用性校验"开关；升级流程固定跳过校验——源是装机时已确认的，
  源服务器临时故障不应卡死应用升级。校验失败时的错误信息现在包含分类与
  报告原文，并给出可操作指引。

## [2.2.1] - 2026-09-21

### 新增

- **Gitee 镜像发版**：Release 流水线在 GitHub 发版后，自动将同一份 fpk 与
  校验文件发布到 Gitee 镜像仓库的发行版（依赖仓库密钥 `GITEE_TOKEN`，
  未配置时自动跳过）。

### 修复

- **洛雪自定义源大部分不可用——沙箱桥与官方客户端契约不兼容（重要）**：逐项
  对照洛雪桌面版官方注入实现（`lx-music-desktop` 的 `preload.js`）修复四处实质
  差异，这些差异会让"在洛雪客户端里可用"的源在本服务里解析失败：
  - `lx.utils.crypto.rsaEncrypt` 误用 PKCS1 填充，官方是 **NO_PADDING + 左侧零
    填充到 128 字节**——依赖该语义加密参数的源（如网易 weapi 系）签名必错，
    相关平台解析全部失败；
  - 沙箱全局过窄：官方脚本跑在完整桌面渲染上下文，`setInterval`、`atob/btoa`、
    `TextEncoder/TextDecoder`、`fetch`、`performance`、`crypto.getRandomValues`
    均可用，沙箱缺任何一个都是 `ReferenceError` 直接打断解析；
  - `lx.request` 的 object 请求体未序列化（官方底层 needle 按 Content-Type 做
    JSON 或 urlencoded 编码，沙箱原样交给 fetch 会直接抛错）；
  - `lx.request` 默认超时 20s → 官方 60s、重定向 3 跳 → 官方 10 跳；响应补
    `statusMessage`/`bytes`；`bufToString` 对齐 binary 语义。
  另对齐官方"初始化前任何未捕获异常视为初始化失败"的语义（此前异步崩溃的
  脚本会假 inited，之后每次解析失败）。
- **慢源被解析超时掐死**：`LX_RESOLVER_TIMEOUT` 默认 4s → 12s（洛雪源多为二级
  转发，实证耗时 3-8s，4s 全部超时并连锁触发熔断，观感即"整源不可用"）；
  播放端 `LX_URL_TIMEOUT` 10s → 20s，代理层每档等待同步放宽到 22s 让降档
  缓存收敛；`musicInfo.interval` 从 `MM:SS` 改为官方的纯秒数字符串。

## [2.2.0] - 2026-09-21

### 新增

- **推荐歌单封面图标**：「每日推荐」与「热门推荐」歌单在左侧歌单列表显示
  封面图——取歌单列表里第一首带可用封面直链的曲目（第一首没封面就依次
  向后找），歌单 `coverId` 以官方 `track_+32hex` 形态下发（官方 App 按
  id 格式过滤，`online:` 原样下发的 coverId 不会被渲染成图标，这正是此
  前"只有文字没有图标"的原因）；服务重启后伪装 id 反解注册表会从推荐
  缓存重建，当天图标不失效。
- **「热门推荐」独立歌单**：`FNMUSIC_RECOMMEND_HOT` 现在注入一个独立的
  「热门推荐」歌单（网易热歌榜 → lxmusic 榜单，榜单原味、不排除已收藏），
  不再只是每日推荐歌单的补位梯队；只开热门、关每日时也会显示；榜单全部
  不可用时不挂空壳歌单。

### 变更

- **每日推荐歌单不再借用榜单补位**：网易未登录时由 LLM/关键词兜底生成，
  榜单内容整体移入「热门推荐」歌单；推荐缓存文件按歌单类型拆分为
  `daily-*.json` / `hot-*.json`，旧格式缓存换日清理后自动重建。
- **推荐歌单全无封面时封面接口返回 404**：客户端显示自带默认样式，不再
  伪造占位图（曲目封面的占位图兜底不变，仍永不 404）。

### 修复

- **构建容器 DNS 失败导致安装中断（重要）**：宿主机 DNS 指向本机（127.x/dnsmasq 等）
  时 Docker 构建容器无法复用宿主 DNS（回退 8.8.8.8，国内不可达），apt 报
  "Temporary failure resolving"；而旧版回退逻辑以 `apt-get update` 退出码判定失败，
  DNS 失败只算 W: 警告、退出码为 0，回退永不触发，最终误报 "Unable to locate
  package"。现在构建层解析探测失败即注入备用公共 DNS（223.5.5.5/119.29.29.29，
  仅构建层内生效），回退判定改为校验 apt 索引是否真正落地，镜像源与官方源均
  不可达时报出带排查建议的明确错误；容器内 pip 安装同样自愈并可回退官方 PyPI；
  安装预检同步新增宿主 DNS 形态提醒。
- **宿主机代理依赖单源安装易断**：新增 `ensure_proxy_deps.sh`（install.sh /
  extend.sh 共用），pip 源按 自定义 `PIP_INDEX` → 阿里云镜像 → 官方 PyPI 依次
  回退并自带重试，全源失败时输出换源与代理排查指引；此前单一清华源不可达即裸报
  "Could not find a version ... (from versions: none)" 中断安装。
- **umask 077 检出下容器无法启动**：`supervisord.conf` 经 COPY 保留检出权限，
  以 600（root:root）进镜像，容器内 appuser 读不了配置，supervisord 起不来、
  安装健康检查超时；现镜像内显式 `chmod 0644`，权限不再依赖构建上下文。以上
  修复同时覆盖 git 安装与 fpk 应用中心安装两条链路（fpk 随下次打包生效）。

## [2.1.1] - 2026-09-21

补齐 bash 编排层与沙箱桥的测试空洞（近期安装/搜索类回归多落在这些无执行测试的
环节），过程中发现并修复三处真实缺陷。

### 修复

- **洛雪自定义源的 `lx.request` 带请求体请求全部失败（重要）**：v2.1.0 给桥
  （bridge.js）加"响应体 JSON 自动转对象"时，解析变量与外层请求体变量同名，
  `let` 提升导致发送前就抛 "Cannot access 'body' before initialization"——
  所有 POST（body/form/formData）请求失败，GET 不受影响所以难以察觉。
- **fpk 升级/卸载备份可能漏掉 `.env` 历史备份**：`packaging/fpk/cmd/_common`
  的数据项通配未加引号，`for` 列表在调用者目录提前展开，恰有 `.env.bak*`
  文件时仓库内的同名文件会被漏备份。
- **WebUI 预览音源到期停止失败后进程漏停**：`preview_reap` 在 supervisorctl
  停止失败时也移除预览表项，偶发失败（如容器重启窗口）后无人重试、进程常驻；
  现在失败保留表项，下一轮清退循环重试。

### 新增

- **fpk 生命周期钩子行为测试（32 项）**：`packaging/tests/test_fpk_cmds.py`
  用桩命令在沙箱里执行真实的 install/upgrade/uninstall 钩子脚本，覆盖备份
  内容、恢复链、向导映射、失败中止等分支。
- **安装脚本深水区测试（30 项）**：`proxy/tests/test_install_edges.py` 覆盖
  基础镜像候选与缓存、extend 验收/回滚、洛雪源校验激活、网易扫码登录的
  关键分支。
- **内置搜索器回放契约测试**：`lxmusic-service/test_searchers_replay.py` 用
  真实录制的酷狗/网易/酷我/QQ 响应 fixture 回放断言解析输出；配套手动录制
  工具 `tests/integration/capture_search_fixtures.py` 便于接口改版后刷新。
- **洛雪源沙箱桥 Node 行为测试（13 项）**：`lxmusic-service/js/test_bridge.js`
  起 http 服务器真实驱动 bridge.js，锁死 console 全 API、重定向/303/超时、
  musicUrl 协议往返等行为；`test_bridge_node.py` 包装进 pytest（无 node
  自动跳过，CI 显式安装 node 保证执行）。
- **实机升级链测试**：`tests/integration/fpk_lifecycle.py` 新增直调
  `upgrade_init`/`upgrade_callback` 阶段（appcenter-cli 不支持升级），
  覆盖"备份→模拟覆盖→恢复重装"全链。
- **熔断半开并发用例**：半开窗口内连续请求只放行一个试探、失败试探顺延窗口。
- CI：test job 显式安装 node（避免桥测试静默跳过）、输出测试覆盖率摘要。

### 变更

- 版本号 `2.1.0` → `2.1.1`。

## [2.1.0] - 2026-09-21

v2.1.0 新增 **fnOS 应用中心 fpk 打包与发布**：本扩展可以作为飞牛第三方应用
一键安装，桌面图标直达管理页，发布流程全自动产出 fpk 到 GitHub Release。

### 新增

- **应用中心 fpk 包（重要）**：新增 `packaging/fpk/` 应用骨架与
  `packaging/fpk/build.sh` 打包脚本（组装仓库主体 → 注入版本 → fnpack 校验 →
  `dist/fnmusic-ext-<版本>.fpk`）。在应用中心「手动安装」fpk 即完成部署：安装向导
  选择初始音源（musicdl / musicbox / lxmusic）与是否立即启用扩展；应用中心内可
  启动/停止（停止=秒级还原官方直连）、卸载前自动把用户数据备份到存储卷根目录
  （`fnmusic-ext-backup-<时间戳>.tar.gz`）；升级钩子自动备份并恢复用户数据。
  生命周期脚本全部复用既有 `install.sh` / `extend.sh` / `restore.sh`，无重复实现。
- **桌面入口**：安装后桌面出现「fnMusic 扩展管理」图标，点击在飞牛桌面窗口内
  打开管理页（iframe 嵌入 `:8774`，仅管理员可见）。
- **fpk 自动发布**：推送 `v<版本>` tag 即自动构建 fpk 并附 SHA-256 发布到
  GitHub Release（`.github/workflows/release.yml`，tag 需与 `VERSION` 一致）；
  CI 每次 push/PR 额外构建一次 fpk 防结构回归（`ci.yml` 新增 `fpk` job）。
- **fpk 结构离线测试**：`packaging/tests/test_fpk_pack.py` 校验 manifest/入口/
  图标/生命周期脚本/组装目录不泄漏开发文件，并固化 fnpack 实测校验规则
  （`initValue` 必须字符串、tips 用 `helpText`、入口名必须以应用名开头）。
- **安装卸载自动测试（实机）**：`tests/integration/fpk_lifecycle.py` 基于
  `appcenter-cli` 对 fpk 做安装→健康断言（WebUI/socket 接管）→停止→启动→
  卸载→清理与数据备份断言的全生命周期验证（飞牛设备上以 root 运行）。

### 变更

- 版本号 `2.0.0` → `2.1.0`；README 与 docs/INSTALL.md 将 fpk 应用中心安装
  提升为推荐安装方式，git clone 脚本安装保留为进阶方式。

## [2.0.0] - 2026-09-20

v2.0.0 是一次架构级重构：三个音源与 WebUI 合并为**单个 Docker 容器、按需加载**，
音源改为**三选一互斥**，洛雪源改为**用户自定义源**，并新增管理 WebUI、音质偏好、
推荐开关、封面兜底与 `.env` 热重载。

### 新增

- **单容器·按需加载（重要）**：musicdl / musicbox / lxmusic / WebUI 四个服务合并进
  一个 `fnmusic-sources` 容器（supervisor 管理），启动时按 `.env` 只拉起当前所选
  音源进程——未启用的音源不再驻留内存，常驻约 100-200MB（原三容器全开约
  200-400MB+）。端口规划不变：8768 / 8770 / 8772 / 8774。
- **管理 WebUI（端口 8774）**：安装时可选（`--webui` / `--no-webui`，非交互默认不装）。
  浏览器内可完成：音源三选一切换（写配置 + 同容器秒级切进程）、musicdl 平台多选、
  网易云扫码登录、洛雪源 URL 测试与保存、音质偏好、边听边存、推荐开关、LLM 配置。
  **无鉴权，仅限可信内网使用**。手机/桌面自适应。
- **洛雪自定义源运行时**：`lxmusic` 音源的播放解析改为运行**你自己提供的洛雪自定义
  源脚本**（官方规范事件子集，容器内 Node 沙箱隔离、仅桥接 HTTP），搜索/歌词/榜单
  继续走内置平台接口。安装向导与 WebUI 均提供「下载→初始化→搜索→解析→探活」
  全链路校验，源状态持久化在 `sources-data/lxmusic/state.json`。
- **音质偏好 `FNMUSIC_QUALITY_MODE`**：`high`（从高到低，原行为）/ `balanced`
  （取中间档，偶数档取中间偏高，失败先向下再向上）/ `smooth`（优先最低），
  作用于网易解析档序、洛雪源档序与 musicdl 同录音候选排序。
- **推荐双开关**：`FNMUSIC_RECOMMEND_HOT` / `FNMUSIC_RECOMMEND_DAILY` 分别控制
  热门榜单与每日推荐（默认均开），推荐链按当前音源自动裁剪。
- **封面永不 404**：封面接口新增多级兜底——酷我 rid 直构 CDN、QQ albummid 直构、
  网易曲库同名曲补全（`FNMUSIC_COVER_ENRICH`，默认开）、本地缓存内嵌图、本地
  占位图池（确定性选取）。此前大量空封面/404 的问题随之消除。
- **`.env` 热重载**：`FNMUSIC_ENV_WATCH=1`（默认开）时代理每 2 秒检测 `.env`，
  白名单键（音源开关/音质/推荐/边听边存/LLM 等）自动生效并清理搜索缓存，
  与 WebUI 切源配合实现免重启调参。
- **musicdl 升级至 `>=2.13.11`**：修复酷狗/网易/QQ 失效端点。平台表新增 `64`
  yinyueku。
- **WebUI 音源预览**：点选未启用的音源即临时拉起（用于扫码/选平台/测源 URL），
  保存时转正常驻；未保存则 5 分钟无操作后自动停止，浏览操作会续期。

### 修复

- **洛雪源测试报「HTTP 500」/ 源无法解析**：野生源脚本普遍调用 `console.group`
  等控制台方法，Node 沙箱此前只提供 5 个基础方法，脚本一调用即崩溃。沙箱现已
  补全 console 标准 API；`lx.request` 响应体对齐桌面版官方行为（可 JSON 解析的
  自动转对象，失败保持字符串）；校验链路的任何异常都转为结构化报告返回（不再
  裸 500），WebUI 对上游非 JSON 响应做防御性解析并显示可读错误；源解析失败导致
  的搜索探活全挂也会报出真实原因，不再误报「搜索无结果」。已实测
  pdone/lx-music-source 的长青（changqing）与全豆要（qdy）源搜索、解析、探活
  全链路可用。
- **洛雪源校验改为多首抽样**：依次用 4 首不同歌手的歌（纯歌名关键词）× 平台
  交集做「搜索 → 解析 → 探活」，任一首成功即判可用（第一首成功就不再继续）；
  单首热门曲缓存/特例不再会把坏源误判为可用，失败报告附抽样明细。
- **启用洛雪源后搜索长期 0 结果（熔断卡死）**：第三方源 API 偶发抽风会触发解析
  熔断，此前冷却期长达 10 分钟且只能被动等待，期间所有搜索直接空结果；换源也
  不清零旧源的熔断状态，健康新源被连带拦截。现熔断打开期间每 30 秒放行一次
  试探（成功立即恢复），换源即清零熔断。
- 安装后 WebUI healthz 超时：镜像缺失 webui 依赖的 proxy 合并模块。
- WebUI 进程状态全部显示「未知」：supervisorctl 存在 STOPPED 程序时退出码非 0
  被误判为连接失败（按需加载下 STOPPED 是常态）。
- `extend.sh` 对已健康部署跳过镜像同步：`git pull` 升级后镜像不重建、修复不生效；
  现健康路径同样幂等重建，`.env` 比容器新时自动重启。

### 变更

- **三音源互斥单选（重要）**：`--sources` 与 `.env` 开关只允许启用一个音源，
  跨音源组合直接报错；旧版多源并存的 `.env` 升级时要求重新单选。换源场景由
  WebUI 秒级切换承担（或改 `.env` 后 `./extend.sh`）。
- **仅支持 Docker 部署**：安装不再提供宿主机（host）音源模式；未安装 Docker 的
  机器直接报错退出（`--mode` 参数已移除）。核心代理仍在宿主机 systemd 运行。
- **`LX_THIRD_PARTY` 及内置第三方解析链移除**：lxmusic 播放解析不再内置聚合链路，
  完全由用户自定义源提供；搜索/歌词/榜单不受影响。
- **数据目录合并**：网易云登录态等迁移至 `sources-data/`（原 `musicbox-data/`
  自动迁移），同时作为洛雪源脚本缓存与状态目录。
- 平台编号 `53`（zhuolin）随上游下线退役：编号永久空缺不复用，新增平台自 `64`
  起表末追加。

### 迁移指引

1. **推荐先还原再安装**（结构变化较大，从干净状态升级最稳妥；`.env` 与数据保留）：
   `git pull` → `./restore.sh`（兼容清理 v1.x 旧容器/宿主机服务）→ `./install.sh`
   按向导重新三选一。直接原地升级（`git pull && ./install.sh`）同样支持；
2. 洛雪用户：准备一个可用的自定义源脚本 URL（安装参数 `--lx-source-url` 或 WebUI 配置）；
3. 旧部署的三个音源容器/宿主机服务会被自动清理，端口由单容器接管；
4. 建议顺手安装 WebUI（`--webui`），此后换源/调参都在浏览器完成。

## [1.7.1] - 2026-09-20

### 修复

- **多副本部署互相破坏的防护（重要）**：同一台机器上若存在两份仓库副本
  （例如正式部署目录 + 测试目录），此前测试目录执行安装/还原可以注册自己的
  systemd 单元、重建同名音源容器，把正式部署在无任何提示的情况下"搬走"，
  随后正式目录既无法扩展也无从得知原因。现在：
  - 新增本机部署登记（`/var/lib/fnmusic-ext/deployment`）：`install.sh` /
    `extend.sh` / `restore.sh` 启动时校验登记，若部署属于其他仍存在的目录则
    拒绝执行并给出指引；确认迁移请追加 `--adopt`（三个脚本均支持）；
    原目录已被删除时不拦截。安装成功、扩展验收通过与还原成功会分别
    写入/清除登记；
  - `extend.sh` 对探活成功的音源不再直接复用：docker 模式核对容器 compose
    归属、host 模式核对宿主机 unit 归属，属于其他目录时报错退出，
    不再静默借用（此前对方副本一删容器，本副本即断连）。
- **安装锁冲突不再静默失败**：安装/扩展/还原共用全局锁。此前锁被占用时
  新命令直接无提示退出，用户完全不知道被谁挡住。现在新命令会识别锁持有者：
  - 若是**本仓库副本**中挂起的旧安装进程（例如向导停在网易云扫码登录），
    自动终止该进程树并接管，日志打印被终止进程的 PID 与命令行；
  - 若是**另一份副本**或无关进程，绝不终止，报告持有者信息后退出。
  判定依据来自 `/proc`（持有锁 fd 的进程 + 命令行/工作目录双重匹配本仓库脚本），
  新增 `takeover.py lock-holders` 子命令。

## [1.7.0] - 2026-09-19

### 新增

- **音源选择升级为「源+平台」粒度（重要）**：此前 lxmusic / musicdl 只能整源启用，
  源内平台（酷我、酷狗、咪咕、QQ 等）无法自行选择。现在安装向导与
  `--sources` 均支持组合写法，例如 `--sources netease,lx-kw,musicdl-kuwo`
  表示：网易云整源 + lxmusic 仅启用酷我 + musicdl 仅启用酷我（两个音源
  容器一起安装，搜索仍按多源并发策略聚合、去重与换源兜底）。
  - 交互向导改为全局平铺编号：每个「源+平台」一个独立 ID（`1` 网易云、
    `2`–`58` musicdl、`59`–`63` lx），向导只列出精选（`1`、`2`–`7`、
    `59`–`62`）；其余平台对照 `musicdl-service/PLATFORMS.md` 在同一输入框
    填写编号（例如 `49` = mdl-gequhai）。不再使用菜单 `0` 二次询问，也
    不再把 `1/2/3` 当作三整源；
  - 全部平台的编号/短名对照表见 `musicdl-service/PLATFORMS.md`
    （与安装脚本内嵌 `SOURCE_PLATFORM_TABLE` 由测试同步校验）；
  - 所选平台自动写入 `.env`（`LX_SOURCES` / `FNMUSIC_ONLINE_SOURCES` /
    `MUSICDL_SOURCES`），容器与宿主机两种部署形态均按所选平台启动，
    代理的搜索、翻页、播放、每日推荐榜单全链路按平台过滤；
  - 整源仍用名字：`--sources musicbox,musicdl,lxmusic` 或 `lx-all` /
    `musicdl-all`；`--sources=1,2,3` 现为网易云 + mdl-酷我 + mdl-酷狗。
- **lxmusic 平台白名单打通代理层**：`lx /api/v1/search` 的 `?sources=`
  参数此前未被代理使用，现按所选平台透传；`online:lx:<平台>:<id>` 条目
  在翻页与播放时按白名单过滤；后端丢失内存 ID 的按平台精确重搜；洛雪
  榜单（酷狗 TOP500/酷我飙升榜/网易新歌速递）按所选平台取交集，交集为
  空时跳过 lx 榜单。
- **配置健壮性**：lxmusic 与 musicdl 服务启动时对平台列表做归一化
  （lx 别名 kugou/kuwo 等归一到 kg/kw；musicdl 短名 kuwo 归一到
  KuwoMusicClient），手改 `.env` 写别名或短名不再静默失效；未知平台
  告警并忽略。`docker-compose.yml` 的 `MUSICDL_SOURCES` 改为由根 `.env`
  覆盖（与 `LX_SOURCES` 同款）。
- **验收按所选平台探测**：`extend.sh` 链路验收对 lxmusic 只探测所选平台、
  musicdl 探测携带平台白名单；所选平台不在 musicdl 服务注册表时输出告警
  （库版本差异，不阻断部署）。
## [1.6.4] - 2026-09-20

### 优化

- **Docker 基础镜像拉取体验优化（重要）**：
  - **本地已有镜像直接短路**：候选镜像在拉取前先通过 `docker image inspect` 进行本地探测，本地已存在时直接选定并跳过拉取，零网络请求，大幅提升升级与重装速度。
  - **拉取进度提示与错误回显**：尝试各镜像源时打印明确的候选序号、镜像引用与超时时间；使用 `--quiet` 抑制拉取进度条的同时保留错误信息，便于快速定位镜像源异常原因。
  - **限制镜像源尝试次数**：新增环境变量 `FNMUSIC_MIRROR_TRIES`（默认 2），最多尝试 2 个镜像源（含缓存源），全败后直接回退至官方源兜底，避免镜像源集体故障时长时间静默等待。

## [1.6.3] - 2026-09-19

### 修复

- **公网/远程访问下在线播放失败（重要）**：通过 fn Connect 中转、https 端口
  映射等公网方式访问时，在线歌曲换源跳转（307）此前会返回一个"协议降级为
  http 且不带端口"的绝对地址（如 `http://<公网域名>/music/...`），该地址在
  公网链路上必然不可达，表现为"公网播放失败、局域网正常、关闭拓展恢复"。
  原因是代理监听在网关后的 Unix socket 上，感知不到客户端的真实协议
  （X-Forwarded-Proto 不被信任），且 fnOS 网关转发时 Host 头不带端口。现在
  跳转地址改为相对路径，由客户端基于自己正在使用的地址解析，公网中转、
  端口映射、局域网各种入口均天然正确。
- **https 关闭或网关端口非标准环境下安装验收误报失败**：验收 6a/6b 的探测
  链此前只尝试 socket → 网关 https 端口 → 443，从不尝试网关 http 端口
  （端口变量读取后未使用），在关闭 https 的机器上唯一可用的网关路径没有被
  探测到；同时 6a 要求未登录响应正文必须含 INVALID TOKEN，部分 fnOS 版本
  返回空正文 401 时会被误判为部署失败并触发回滚。现在补上网关 http 端口
  回退，未登录断言兼容按 401 状态码判定，失败信息会附上观测到的状态码
  便于诊断。

## [1.6.2] - 2026-09-19

### 修复

- **手机 App 端在线歌曲收藏/播放历史全面兼容（重要）**：手机 App 对歌曲
  数据有严格校验，在线歌曲此前在 App 端表现为收藏列表/播放历史空白、
  条目消失、红心点亮后自动熄灭（网页端一切正常）。现在插件会把下发给
  客户端的在线歌曲编号统一转换成与官方完全一致的格式（含歌手、专辑、
  封面编号），并在所有入口自动换回真实编号路由；歌名字段缺失时按官方
  习惯留空而非填 0/假值；播放历史条目会自动补齐歌名歌手（来源：播放
  事件自带信息或搜索会话缓存）。该转换纯内存进行，不落盘、不改动官方
  数据，还原（restore）后自然消失，无需任何清理。
- **收藏状态实时同步**：歌曲详情、搜索结果、播放历史中的"已收藏"标记
  现在返回真实状态，App 点亮红心后不再被刷新回未收藏。
- **重启 fnOS 后插件接管失败**：开机时官方音乐服务可能晚于插件启动，
  此前接管会因"无法确认服务状态"而安全退出，需要手动重启插件。现在
  会等待官方服务就绪（最长 40 秒）后再接管，重启 fnOS 即可自动恢复。

## [1.6.1] - 2026-09-18

### 修复

- **手机 App 播放历史/收藏列表空白（严重）**：播放或收藏在线搜索的歌曲后，
  手机 App 端整个播放历史或收藏列表可能变为空白（网页端正常）。原因是插件
  下发的在线歌曲数据格式此前只对齐了网页端，且登录身份校验在 App 的票据
  用法下可能误判为未登录。现在：在线条目合并前先以官方返回的真实歌曲数据
  作结构模板，字段与官方完全一致；歌手/专辑缺失时有兜底文案，不再出现空
  歌手、空专辑；身份校验误判时降级为仅显示官方列表，不再让整页空白。
- **手机 App 无法播放在线歌曲（含已缓存歌曲）**：补齐两处 App 播放器兼容
  缺口——播放地址接口现在支持 HEAD 探测请求与路径形式的歌曲编号，此前这
  两种请求会被错误转交给官方服务导致播放失败。
- **播放历史记录更完整**：客户端上报播放事件时若携带歌名、歌手、专辑、
  时长，会一并记录；历史条目不再只有编号没有标题。

### 新增

- **请求诊断日志**：代理以固定格式记录每个 `/music` 请求的方法、路径、
  状态码与客户端标识（不含查询参数与敏感信息），便于远程排查 App 端
  兼容问题。

## [1.6.0] - 2026-09-17

### 修复

- **官方应用升级后"还原/重装"死锁（严重）**：官方应用升级或重启时，新版实例会
  直接绑回原 socket 路径，而升级前的旧官方进程可能残活在上游路径
  （`trim_music_upstream.socket`）成为孤儿——重启官方应用也杀不掉它。此前
  `restore.sh`（含 `--full`）与重装在这种状态下都会拒绝执行，形成永久死锁。
  现在只要原路径已被内核验证为官方进程直连（播放不受任何影响），恢复与重装
  会直接回收上游孤儿 socket 文件：仅删除路径名、不动进程本身，日志会提示
  残留进程的 PID 供按需清理。真正无法识别身份的歧义布局仍保持拒绝。

### 新增

- **边听边存开关与保存路径配置**：新增 `FNMUSIC_TEE_SAVE_ENABLED`（默认开）、
  `FNMUSIC_TEE_SAVE_DIR`（默认空=自动探测飞牛共享曲库；配置后以配置为准，
  路径不可用自动回退默认并告警）。
- **关闭边听边存时的滚动试听缓存**：新增 `FNMUSIC_TEE_CACHE_MAX`（默认 2），
  仅在边听边存关闭时生效——完整试听的歌曲以 `online_源_歌曲id` 命名滚动缓存在
  cache 目录（重播秒开、不进官方曲库），超出数量自动淘汰最旧的；
  `restore.sh` 默认还原与 `--full` 均会清理这些滚动缓存（已存入曲库的歌曲与
  曲库引用 `.ref` 不受影响）。

## [1.5.0] - 2026-09-11

### 新增

- **音源原生每日推荐（取代大模型推荐）**：不再强制依赖大模型，
  每日推荐默认采信音乐源自身的推荐能力，按优先级单链逐级补齐 20 首：
  1. **网易真·每日推荐**：网易云音源启用且已登录时（支持手机 App 扫码），
     直接调用网易云每日推荐（`weapi`，个性化）；
  2. **网易免登录榜单**：网易云启用但未登录（或第 1 级不足）时，
     自动降级为网易热歌榜（NEMbox 内置 `toplist`，匿名可用）；
  3. **洛雪免登录榜单**：洛雪音源启用时，聚合酷狗移动端 TOP500、
     酷我 kbang 飙升榜与网易新歌速递（2026-09 全部实测免登录存活，
     经 Range 探活验证确保完整可播时长）；
  4. **大模型兜底**：仅当网易音源未启用且在 `.env` 中配置了
     `FNMUSIC_LLM_*` 时才调用大模型生成候选，保留随时切回能力；
  5. **关键词兜底**：全链路故障时由历史种子歌手与热门池搜索保底。
- **平台 ID 直连详情与可播性验证**：音源原生推荐直接返回平台歌曲 ID，
  直连批量详情接口并复用服务端 `filter_playable_song_ids`（自动过滤
  收费/VIP/仅试听片段），取代此前"大模型出歌名 → 三源搜索 → 模糊匹配"
  的高损耗链路，推荐曲目命中率与可播性大幅提升。
- **musicbox-service 新增推荐与榜单路由**：
  - `GET /api/v1/recommend/songs`：调用 NEMbox CLI `recommend songs`；
    未登录时结构化透传 `not_logged_in` 错误供代理降级；
  - `GET /api/v1/toplist`：支持查询榜单列表及指定榜单曲目水化。
- **lxmusic-service 新增免登录榜单路由**：
  - `GET /api/v1/recommend`：聚合 kg/kw/wy 三源免登录榜单，
    对齐现有 search item 统一曲目格式。
- **可观测性增强**：`/_ext/healthz` 与每日缓存 payload 记录各用户
  当日推荐的生效来源梯队（`tiers: ["netease-daily", ...]`），排障更清晰。

## [1.4.0] - 2026-09-11

### 新增

- **适配 2026-09-11 升级后的新版飞牛音乐**：核心接管机制与全部扩展功能
  （在线搜索合并、在线播放与边播边存、歌词、封面、多用户收藏、每日推荐、
  播放历史）与新版官方应用完全兼容，旧版官方应用同样可用。
  官方应用升级后若发现扩展未生效，重新执行 `./extend.sh` 即可恢复
  （会自动拉起音源并完成接管与验收）。

### 修复

- **重装不再清空大模型配置**：安装向导对"每日推荐"答 N 或留空时，
  此前会把 `.env` 中已保存的 `FNMUSIC_LLM_*` 以空值显式覆盖，导致每次重装都
  需重新填入 API Key。现改为：答 N/留空一律保留既有配置；仅显式传入
  `--disable-recommend` 才清除。
- **restore.sh 语义重设计**：
  - 默认：还原官方直连，同时停止并删除音源容器/宿主机服务，
    但完整保留 `.env` 与全部数据（网易云登录、缓存、在线收藏、播放历史、
    推荐缓存）——重新启用后无需重新填写任何配置；
  - `--full`：彻底清理——额外删除 `.env`（含历史备份）与上述全部数据目录
    及虚拟环境，仅保留代码本身，用于完全重置。

### 测试

- 新增：大模型配置保留语义、restore 清理范围（该删的删净、代码与未知文件保留）
  等行为级回归测试；修正个别用例在真实主机上的环境敏感断言。

## [1.3.1] - 2026-09-10

### 修复

- **修复接管中断后"装不上也还原不了"的死锁（严重）**：`takeover.py` 原先要求 socket 文件
  必须在归属记录（`ownership.json`）中登记过才允许删除，且只允许删除 `stale` 状态的文件。
  一旦上一次接管失败留下未登记的僵尸 socket 文件（官方 socket 已停放在
  `trim_music_upstream.socket`，而 `trim_music.socket` 是一个无人监听的残留文件），
  `publish` 与 `restore` 会同时以 `unrecorded/replaced socket; preserved` 失败并永久互锁。
  现改为：对**可证明不可达**的 socket 文件（`connect()` 返回 `ECONNREFUSED`、同一 inode
  连续两次观测一致）一律回收，并打印审计行；仍然拒绝删除任何存活（含代理自身）或身份不明
  的 socket 文件。安装与还原在任何历史失败残留之后都可继续。
- **修复 supervisor 回滚异常掩盖真实失败原因**：`supervise()` 的 `finally` 中执行回滚，
  回滚自身的异常会覆盖原始异常，导致日志只显示 `unrecorded/replaced socket; preserved`，
  真正的失败原因（例如拓扑冲突）被吞掉。现改为分别捕获并串行上报
  `<原始原因>; rollback failed: <回滚原因>`，且正常停止（SIGTERM）时不再被误判为失败。
- **修复归属记录不一致导致的死锁**：官方应用重启（PID/inode 变化）或代理崩溃后，
  `remember`/`publish`/`restore` 原先会以"归属变更"直接失败。因两类角色均由内核身份
  （`SO_PEERCRED` + `/proc/<pid>/exe`、`/_ext/livez` + peer PID）实证，记录仅作缓存，
  现改为带 `[takeover] note:` 审计行地采纳新的存活身份，并在移动后校验终态。
- **`restore-plan` 覆盖全部可恢复拓扑**：新增 `vacant-target-repair`（僵尸 target + 官方
  upstream）与 `official-direct`（含 upstream 僵尸文件清理）两类计划；`restore()` 会清理
  僵尸 upstream，并在确实不存在任何官方监听者时明确报错
  `no official listener present; restart the official music app`，不再留下自相矛盾的布局。
- **就绪窗口对齐**：supervisor 内部就绪判定由 30s 放宽到 55s，保持在 unit
  `ExecStartPost` 的 65s 之内，慢启动不再被内部判定抢先回滚。
- 回归测试同步更新：新增僵尸 target/upstream 回收、官方重启采纳、双活冲突下的
  错误串行上报等用例。

## [1.3.0] - 2026-09-09

### 新增

- **lxmusic 音源新增 tx（QQ音乐）与 kw（酷我）子源**：tx 搜索采用 QQ 官方免登录接口
  （musicu.fcg），kw 搜索采用酷我官方 r.s 老接口；直链解析经第三方链路 + Range 探活验证。
  `LX_SOURCES` 默认值由 `kg,wy,mg` 扩展为 `kg,wy,mg,tx,kw`。
- **第三方解析链路层（移植自洛雪社区聚合源 qdy v9.3 的链路清单与多链路回退架构）**：
  数据驱动注册表 + 连续失败熔断（3 次失败暂停 10 分钟）+ 链路健康状态暴露于 `/healthz`。
  2026-09-09 对 qdy 全部 10 条链路逐条实测后仅移植存活链路：长青 kw（302→酷我 CDN 无损
  FLAC）与溯音咪咕（320k 直链+歌词）；星海/念心/溯音 oiapi/汽水等 8 条已死链路不移植，
  后续复活时在注册表追加即可。`LX_THIRD_PARTY=0` 可整体关闭（退化为纯官方免登录直连）。
- **可播性验证（"搜得到必能播"）**：kg/wy 的 VIP/付费曲目不再按 pay_type/fee 元数据一票
  否决，改为"官方解析→第三方链路→Range 探活（200/206 且非 HTML）+ 试听碎片体积防护"全链路
  验证，通过才返回并携带 `verified: true` 标记；kw/tx/mg 搜索结果同样全部探活。试听片段、
  无版权、VIP 拦截（fail_process=4）等真不可播标记的过滤保持不变。

### 变更

- proxy 的 `is_playable_online_track` 对 `verified` 条目跳过收费元数据拦截（pay_type/price/
  fee），试听/无流/坏 URL 校验全部保留——服务端已用真实探活证明可播，元数据不再作为可播性
  的代理判断。
- mg 咪咕搜索的逐曲解析升级为完整"解析+探活"（官方接口失败自动回退溯音咪咕链路），此前仅
  校验 URL 存在性、未做存活性探测。
- lxmusic-service 版本号 1.0.0 → 1.1.0；extend.sh 子源探测清单扩展为 kg/wy/mg/kw/tx。

### 修复

- **升级合并不再丢失用户 .env 注释**：`env_merge` 此前在增量合并时静默丢弃用户手写的
  注释/非赋值行；现按原顺序去重后保留在文件末尾（连续合并不堆积），键值合并规则不变。

### 工程与测试

- **新增 GitHub Actions CI**（`.github/workflows/ci.yml`）：push 到 main/dev 与所有 PR 自动
  执行 shell 语法检查、Python 编译检查与全量 pytest（Python 3.11/3.13 矩阵）；另附
  shellcheck 静态检查（error 级，仅报告不阻断）。
- **测试套件单命令化**：新增 `pytest.ini`，`python -m pytest` 一条命令跑全部 proxy +
  各音源服务测试；修复 musicbox 与 lxmusic 测试的顶层 `app` 模块名冲突（改为独立模块名
  显式加载）；musicdl 假慢源线程改可中断睡眠，测试进程退出不再挂起约 26 秒（全套约 13 秒）。
- **版本断言测试去硬编码**：`test_version_env` 改为动态读取 `VERSION` 文件比对，升级版本
  不再需要同步修改测试。
- **补齐 musicbox 服务 9 项端点测试**：healthz、播放地址（音质白名单/CLI 参数）、歌曲信息、
  歌手/专辑/歌单、歌词（成功/上游异常）、登录状态与扫码轮询、上游失败 502 与超时 504 信封。

### 已知边界

- tx（QQ音乐）直链解析当前无存活第三方链路（qdy 的 4 条 tx 链路于 2026-09-09 实测全部失效），
  tx 搜索会因探活全部失败而返回空结果但不报错；第三方链路复活后在 `THIRD_PARTY_CHAIN`
  注册表登记即自动恢复。
- kw（酷我）免登录歌词接口已全部失效，歌词暂返回空，不影响播放。
- 第三方链路属社区公益性质，随时可能失效；熔断器保证失效链路自动旁路，最坏情况退化为
  现有官方免登录能力（kg/wy/mg 免费曲），不会出现"搜得到播不出"。

## [1.2.3] - 2026-09-08

### 新增

- **Docker 构建基础镜像源自动探测回退**：fnOS 等系统在 Docker daemon 全局配置的镜像加速器
  （如 `docker.fnnas.com`）异常（401/超时）时，BuildKit 解析 `python:3.13-slim` 元数据失败且不会
  回退官方源，`docker compose up --build` 随即失败（`failed to resolve source metadata ... 401 Unauthorized`）。
  新增 `ensure_base_image.sh`：**国内镜像优先**（完整镜像源引用直连对应仓库，绕开只拦截
  docker.io 短引用的 daemon 加速器，以真实 `docker pull` 验证），逐个尝试
  docker.1ms.run / docker.m.daocloud.io / docker.1panel.live / hub.rat.dev（`FNMUSIC_DOCKER_MIRRORS`
  可覆盖），全部失败再兜底官方 `python:3.13-slim`（daemon 加速器链路在国内网络下常慢/不稳），
  结果缓存到 `.env` 的 `FNMUSIC_BASE_IMAGE` 并由 compose `build.args` 自动读取；install.sh 安装、
  extend.sh 自愈重建、手动 `docker compose up -d --build` 三条路径全部生效，后续运行先验证缓存、
  失效自动重新探测。全程不修改系统 Docker 配置，仅本应用构建生效；也可通过 `BASE_IMAGE` 环境变量
  或直接编辑 `.env` 手动指定。

### 变更

- 三个音源镜像构建内 `pip install` 默认接入清华 PyPI 源（`.env` 的 `FNMUSIC_PIP_INDEX` 可覆盖），
  与宿主机模式安装惯例对齐。
- musicdl 镜像 apt 层默认接入清华镜像（`FNMUSIC_APT_MIRROR` 可覆盖）：仅在构建层内临时替换
  `deb.debian.org`，apt update 失败（15s 超时快速判定）自动回退官方源重试，安装完成后恢复
  官方源——最终镜像与宿主机 apt 配置不受影响；国内网络下 ffmpeg 及其依赖不再长时间卡在
  deb.debian.org 慢速下载。

## [1.2.2] - 2026-09-08

### 修复

- **Docker 安装在受限 umask 环境下启动失败的严重问题**：三个音源镜像此前直接继承仓库检出文件的权限位，
  在 umask 077 环境（root shell、`sudo git clone` 等）下检出的 `app.py` 为 600，进镜像后为
  `root:root 0600`，容器内非 root 的 `appuser` 无法读取，uvicorn 启动即抛
  `PermissionError: [Errno 13] Permission denied: '/app/app.py'` 并随 `restart: unless-stopped` 无限重启。
  现镜像内源码统一 `--chown=appuser:appuser` 且权限 0644，与宿主机文件权限完全解耦（`COPY --chmod`
  仅 BuildKit 支持，故采用兼容新旧构建器的 `--chown` + `RUN chmod` 方案）。
- 修正 musicbox Dockerfile 中 `chown` 早于 `COPY` 执行而对源码文件不生效的问题。

### 变更

- `install.sh` 构建前对服务源码做权限归一化（非致命兜底），避免受限 umask/属主影响构建上下文。
- `docs/INSTALL.md` 新增「常见问题排查」章节，含上述报错的说明与升级方法。

## [1.2.1] - 2026-09-08

### 修复

- 移除生产安装中多余的 pytest 测试依赖。

## [1.2.0] - 2026-09-07

### 新增

- 安装收尾集成网易云终端扫码登录流程（ASCII 二维码过期自动刷新 + 登录状态轮询）。

## [1.1.2] - 2026-09-07

### 新增

- 全音源严格可播过滤与直链探活防线校验升级。

### 优化

- 多音源搜索 3s 首屏与 5s 首响兜底机制，缓存延长至 7 天。

## [1.1.1] - 2026-09-07

### 修复

- 过滤收费不可播歌曲，重构酷狗直链解析。
- 安装/还原流程加固与多音源搜索容错增强。

## [1.1.0] - 2026-09-07

### 新增

- 第三音源：洛雪音乐源（lxmusic，酷狗/网易/咪咕免登录解析）。

## [1.0.1] - 2026-09-07

### 新增

- 项目版本管理与安装配置增量合并机制，音源超时与自适应降级。

### 修复

- 彻底修复网易云 XDG 目录缺失导致子进程崩溃，支持命令行终端直接显示登录二维码。
- 重构验收试播逻辑，支持多音源平等遍历与多关键词重试。
- extend 与端口 5667 解耦，通过 UDS 检查安全判定启用状态。

## [1.0.0] - 2026-09-04

### 新增

- fnmusic-ext 首个发布版本：musicdl / musicbox 双音源，Docker 与宿主机双模式部署，一键安装向导与 fnOS 代理扩展接管。
