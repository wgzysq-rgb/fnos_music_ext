#!/usr/bin/env bash
# fnmusic-ext 播放/取流问题只读诊断
#
# 用途：定位「曲目能显示、但一点播放就跳到下一首」这类问题。
# 全部为只读操作：不修改配置、不重启服务、不删任何文件。
#
# 用法：
#   sudo bash scripts/diagnose_playback.sh
#   sudo bash scripts/diagnose_playback.sh lx:wy:1415111009     # 附带一次直链解析测试
#   sudo bash scripts/diagnose_playback.sh lx:tx:0039MnYb0qxYhV
#
# 第二个参数（曲目 id）怎么拿：在飞牛音乐里打开出问题的推荐歌单，
# 用浏览器开发者工具看 playlist 曲目列表请求的返回，条目里 `id` 字段
# 形如 `lx:wy:<网易songid>` / `lx:tx:<QQ songmid>` / `lx:kg:<hash>`。
# 拿不到也可以不传，脚本其余部分照样有诊断价值。

CONTAINER="${FNMUSIC_CONTAINER:-fnmusic-sources}"
LX_PORT="${FNMUSIC_LX_PORT:-8772}"
OUT="${FNMUSIC_DIAG_OUT:-/tmp/fnmusic-playback-diag-$(date +%Y%m%d-%H%M%S).txt}"

: > "$OUT" 2>/dev/null || OUT="/dev/null"

say() { printf '%s\n' "$*" | tee -a "$OUT"; }
sec() { say ""; say "===== $* ====="; }

DOCKER_MODE=0
if command -v docker >/dev/null 2>&1; then
    if docker info >/dev/null 2>&1; then
        DOCKER_MODE=1
    elif command -v sudo >/dev/null 2>&1 && sudo docker info >/dev/null 2>&1; then
        DOCKER_MODE=2
    fi
fi

d() {
    if [ "$DOCKER_MODE" = "2" ]; then sudo docker "$@"; else docker "$@"; fi
}

say "fnmusic-ext 播放诊断报告"
say "生成时间: $(date '+%Y-%m-%d %H:%M:%S %z')"
say "输出文件: $OUT"

if [ "$DOCKER_MODE" = "0" ]; then
    say "!! 无法连通 Docker daemon，请用 sudo 运行，或把当前用户加入 docker 组。"
    exit 1
fi

# ---------------------------------------------------------------- 1. 容器 --
sec "1. 容器状态"
d ps -a --filter "name=^/${CONTAINER}$" \
    --format 'name={{.Names}}  status={{.Status}}  image={{.Image}}' 2>&1 | tee -a "$OUT" || true

if [ -z "$(d ps -q --filter "name=^/${CONTAINER}$" 2>/dev/null)" ]; then
    say "!! 容器 $CONTAINER 不在运行中。先确认应用已启动："
    say "   sudo docker logs --tail 100 $CONTAINER"
    exit 1
fi

sec "2. 容器内关键配置"
d exec "$CONTAINER" sh -c '
  echo "FNMUSIC_HOME=$FNMUSIC_HOME"
  echo "FNMUSIC_CACHE_DIR=$FNMUSIC_CACHE_DIR"
  echo "FNMUSIC_LX_ENABLED=$FNMUSIC_LX_ENABLED"
  echo "FNMUSIC_LX_SOURCES=$FNMUSIC_LX_SOURCES"
  echo "FNMUSIC_STREAM_PROBE=$FNMUSIC_STREAM_PROBE"
  echo "FNMUSIC_RECOMMEND_PLAYLIST=$FNMUSIC_RECOMMEND_PLAYLIST"
  echo "--- 进程 ---"
  ps -eo pid,etime,comm 2>/dev/null | grep -E "node|python|ffmpeg|supervisord" | grep -v grep
' 2>&1 | tee -a "$OUT" || true

# --------------------------------------- 2b. 边听边存（tee）与封面缓存 --
sec "2b. 边听边存（tee）配置、曲库探测与落盘"
d exec "$CONTAINER" sh -c '
  echo "FNMUSIC_TEE_SAVE_ENABLED=$FNMUSIC_TEE_SAVE_ENABLED"
  echo "FNMUSIC_TEE_SAVE_DIR=$FNMUSIC_TEE_SAVE_DIR"
  echo "FNMUSIC_LIBRARY_DIR=$FNMUSIC_LIBRARY_DIR"
  echo "FNMUSIC_MUSIC_DB=$FNMUSIC_MUSIC_DB"
  echo "FNMUSIC_LIBRARY_SCAN_PATH=$FNMUSIC_LIBRARY_SCAN_PATH"
  echo "FNMUSIC_COVER_PROXY=$FNMUSIC_COVER_PROXY"
  echo "FNMUSIC_COVER_CACHE_DIR=$FNMUSIC_COVER_CACHE_DIR"
  echo "--- music.db 曲库探测 ---"
  db="${FNMUSIC_MUSIC_DB:-/usr/local/apps/@appdata/trim.music/db/music.db}"
  if [ -f "$db" ]; then
    echo "music.db 存在: $db"
    if command -v sqlite3 >/dev/null 2>&1; then
      sqlite3 "file:$db?mode=ro" "SELECT path FROM shared_library ORDER BY id;" 2>&1
    else
      echo "(容器内无 sqlite3，无法直接读 shared_library；以第 5 节日志里的探测告警为准)"
    fi
  else
    echo "!! music.db 不存在: $db"
    echo "   曲库探测必失败 → 边听边存会静默落到 cache 目录（文件名是哈希、不进官方曲库），"
    echo "   表现就是「边听边存失效/找不到存哪了」。"
    echo "   修法：在 .env 设置 FNMUSIC_MUSIC_DB=<真实 music.db 路径>，"
    echo "   或 FNMUSIC_LIBRARY_DIR=<飞牛音乐共享曲库目录> 后重启容器。"
  fi
  echo "--- 落盘候选目录最近 15 项 ---"
  for dir in "$FNMUSIC_TEE_SAVE_DIR" "$FNMUSIC_LIBRARY_DIR" \
             "${FNMUSIC_CACHE_DIR:-/data/cache}"; do
    [ -n "$dir" ] && [ -d "$dir" ] || continue
    echo "[$dir]"
    ls -lt "$dir" 2>/dev/null | head -15
  done
  echo "--- 封面缓存目录 ---"
  croot="${FNMUSIC_COVER_CACHE_DIR:-${FNMUSIC_CACHE_DIR:-/data/cache}/covers}"
  if [ -d "$croot" ]; then
    echo "[封面缓存] $croot 共 $(ls "$croot" 2>/dev/null | wc -l) 个文件"
    ls -lt "$croot" 2>/dev/null | head -8
  else
    echo "(尚无封面缓存目录 $croot —— 2.7.7+ 会在首次封面代理时自动创建)"
  fi
' 2>&1 | tee -a "$OUT" || true
say ""
say "边听边存解读："
say "  FNMUSIC_TEE_SAVE_ENABLED 为 false/空   → 开关被关了（.env 检查）"
say "  music.db 不存在 / shared_library 为空  → 探测失败，文件落在 cache 哈希名文件里"
say "  FNMUSIC_LIBRARY_SCAN_PATH 为空          → 正常（默认禁用，不影响落盘，只影响官方重扫）"

# ------------------------------------------------------------ 3. 洛雪音源 --
sec "3. 洛雪音源状态（播放链的关键依赖）"
say "-- /api/v1/source --"
curl -s --max-time 8 "http://127.0.0.1:${LX_PORT}/api/v1/source" 2>&1 | tee -a "$OUT" || true
say ""
say "-- /api/v1/source 内联解读 --"
say "  若 data.active 为 false / 为空，或 data 里没有已导入的源脚本，"
say "  说明洛雪源未激活——此时所有 lx 曲目都解析不出直链，表现为一点就跳下一首。"
say "  修法：飞牛音乐 → 扩展 WebUI → 音源管理，重新导入并激活自定义源。"

if [ -n "${1:-}" ]; then
    TRACK_ID="$1"
    sec "4. 直链解析测试（直接打 lxmusic-service，绕过 App）"
    say "曲目 id: $TRACK_ID"
    for q in lossless 320k 128k; do
        say "-- quality=$q --"
        curl -s --max-time 30 -w '\n[http_code=%{http_code}]\n' \
            "http://127.0.0.1:${LX_PORT}/api/v1/track/url?id=${TRACK_ID}&quality=${q}" \
            2>&1 | tee -a "$OUT" || true
    done
    say ""
    say "解读：三个音质档全都不 ok → 洛雪源/源脚本的问题（不是扩展本身）；"
    say "      某一档 ok → 播放链是通的，问题在别处（看第 5、6 节的日志）。"
else
    sec "4. 直链解析测试"
    say "（未提供曲目 id，跳过。加上参数可做这一步：bash $0 lx:wy:1415111009）"
fi

# --------------------------------------------------------------- 5. 日志 --
sec "5. 最近的播放相关日志（取流/解析/转码/边听边存/封面/错误）"
d logs --tail 3000 "$CONTAINER" 2>&1 \
    | grep -Ei 'stream|transcode|lx|resolve|url|tee|finalize|cover|曲库|落盘|跳|404|4 0 4|failed|error|warn|自愈' \
    | tail -150 | tee -a "$OUT" || true

say ""
say "关注这几行："
say "  stream probe: GET online:lx:...  → App 确实来取流了（说明 metadata 这步是通的）"
say "  resolve_lx_url 全档失败 ...       → 直链解析不出，洛雪源侧问题"
say "  stream 取流失败 ... 候选全部不可用 → App 会跳过该曲目"
say "  transcode state 自愈 ...          → 转码分片数被自动校正（正常，会修好旧缓存）"
say "  没有 stream probe 行               → App 根本没来取流，问题在曲目详情/客户端侧"
say ""
say "边听边存（tee）关注这几行："
say "  stream probe: ... range=None / range='bytes=0-'  → 该次播放符合落盘条件"
say "  stream probe: ... tee_eligible=True              → 落盘资格判定通过"
say "  tee finalize done for <guid>: dest=<路径>        → 完整接收并落盘成功（看 dest 在哪）"
say "  Background full fetch saved/failed ...           → 切歌后的后台续传结果"
say "  曲库探测失败：music.db 不存在 / shared_library 无可用路径"
say "     → 落盘退化到 cache 目录（文件名哈希、不进官方曲库），需要修 FNMUSIC_MUSIC_DB/库目录"
say "  Library scan triggered/failed ...                → 落盘后通知官方重扫（默认禁用，无此行属正常）"
say ""
say "封面（cover probe）关注这几行："
say "  cover probe: GET coverId=<歌单guid> ... -> 200 local-file  → 命中 NAS 缓存，正常"
say "  cover probe: ... -> 302 redirect-><CDN>                    → 代理没取到，回落直链"
say "  cover probe: ... -> 200 inline-image(占位图...)            → 走的占位图兜底"
say "  playlist cover <guid> kind=daily ... -> placeholder(no usable cover)"
say "     → 该歌单快照整单都没有可用封面（在线音源构建的歌单常见）"
say "  netease playlist cover ... -> placeholder(no registered cover)"
say "     → 网易歌单封面直链未登记（重启后反查表未重建）"

# --------------------------------------------------- 5b. 封面缓存与歌单封面 --
sec "5b. 封面探针摘要与缓存规模"
say "-- cover probe 产物分布（最近 3000 行日志）--"
d logs --tail 3000 "$CONTAINER" 2>&1 | grep -E "cover probe:" | tail -40 | tee -a "$OUT" || true
say ""
say "-- 歌单封面兜底记录 --"
d logs --tail 3000 "$CONTAINER" 2>&1 | grep -E "playlist cover .*(placeholder|local-file)" | tail -20 | tee -a "$OUT" || true
say ""
d exec "$CONTAINER" sh -c '
  root="${FNMUSIC_COVER_CACHE_DIR:-${FNMUSIC_CACHE_DIR:-/data/cache}/covers}"
  if [ -d "$root" ]; then
    echo "封面缓存目录: $root"
    echo "文件数: $(ls "$root" 2>/dev/null | wc -l)   占用: $(du -sh "$root" 2>/dev/null | cut -f1)"
    ls -lt "$root" 2>/dev/null | head -6
  else
    echo "(尚无封面缓存目录 $root)"
  fi
' 2>&1 | tee -a "$OUT" || true
say ""
say "解读：若歌单封面全是 placeholder，说明该歌单快照没有可用的封面来源"
say "      （在线音源搜索结果通常不带 cover_url）；升级到 2.7.8+ 后应能自动"
say "      用首曲的在线封面/登记表封面，仍不行则把本节内容贴回对话。"

# --------------------------------------------------- 6. 转码 HLS 一致性 --
sec "6. 转码 HLS 会话一致性（声明分片数 vs 磁盘实际分片数）"
d exec "$CONTAINER" sh -c '
  root="${FNMUSIC_CACHE_DIR:-/data/cache}/hls"
  [ -d "$root" ] || { echo "(无 hls 缓存目录 $root，说明还没走过标准音质转码)"; exit 0; }
  for d in "$root"/*; do
    [ -d "$d" ] || continue
    n=$(ls "$d" 2>/dev/null | grep -cE "^[0-9]{5}\.m4s$")
    st="-"; dc="-"
    if [ -f "$d/state.json" ]; then
      st=$(sed -n "s/.*\"status\"[[:space:]]*:[[:space:]]*\"\([a-z]*\)\".*/\1/p" "$d/state.json")
      dc=$(sed -n "s/.*\"declared_count\"[[:space:]]*:[[:space:]]*\([0-9]*\).*/\1/p" "$d/state.json")
    fi
    flag=""
    [ "$st" = "done" ] && [ -n "$dc" ] && [ "$dc" != "$n" ] && flag="  <== 不一致(下次播放会自动自愈)"
    echo "$(basename "$d")  status=$st  declared=$dc  actual=$n$flag"
  done
' 2>&1 | tail -60 | tee -a "$OUT" || true

# ------------------------------------------------------------ 7. 资源 --
sec "7. 转码进程与负载"
d exec "$CONTAINER" sh -c 'ps -eo pcpu,pmem,etime,args 2>/dev/null | grep ffmpeg | grep -v grep' 2>&1 | tail -15 | tee -a "$OUT" || true
say "（ffmpeg 常驻多个且 CPU 打满时，标准音质会断续；与本文档主线无关但值得留意）"
d exec "$CONTAINER" sh -c 'cat /proc/loadavg 2>/dev/null' 2>&1 | tee -a "$OUT" || true

sec "8. 磁盘空间"
d exec "$CONTAINER" sh -c 'df -h "${FNMUSIC_CACHE_DIR:-/data}" 2>/dev/null' 2>&1 | tee -a "$OUT" || true

say ""
say "===== 报告结束 ====="
say "报告文件: $OUT"
say "把该文件内容贴回对话即可。"
