#!/usr/bin/env bash
# fnmusic-ext 播放 / 边听边存 / 封面 只读诊断
#
# 用途：定位「一点播放就跳下一首」「边听边存好像失效了」「列表没有封面」
# 这类问题。全部为只读操作：不修改配置、不重启服务、不删任何文件。
#
# 用法（应用中心 fpk 安装后在 NAS 上跑）：
#   sudo bash /var/apps/fnmusic-ext/target/repo/scripts/diagnose_playback.sh
#   sudo bash /var/apps/fnmusic-ext/target/repo/scripts/diagnose_playback.sh lx:wy:1415111009
#   sudo bash /var/apps/fnmusic-ext/target/repo/scripts/diagnose_playback.sh lx:tx:0039MnYb0qxYhV
# git clone 方式安装时路径为 <仓库>/scripts/diagnose_playback.sh。
# 注意 /var/apps/fnmusic-ext/target 是软链 → /vol1/@appcenter/fnmusic-ext，
# 用 find 定位要加 -L（find 默认不跟随软链，会什么都搜不到）。
#
# ★★★ 日志要看两处，只看一处必然得出错误结论 ★★★
#
#   ① 拦截代理（本扩展本体）跑在 NAS 宿主机的 systemd 服务里 —— 不是容器！
#        日志 → journalctl -u fnmusic-ext
#        「stream probe」（取流探针）、「tee finalize done」（边听边存落盘）、
#        「cover probe」（封面命中分支）、「playlist cover」（歌单封面兜底）、
#        启动时的「[自检] 曲库目录 / 边听边存落盘目录」全都在这里。
#   ② 音源跑在容器 fnmusic-sources 里。
#        日志 → docker logs fnmusic-sources
#        「洛雪/音源解析」「转码 ffmpeg」「历史数据迁移」「supervisor」在这里。
#
#   所以：docker logs 里永远看不到 stream probe / tee finalize / cover probe。
#   本脚本第 5 节读代理日志（journalctl），第 6 节读容器日志（docker logs）。
#
#   某节内容为空，最常见的解释是「升级后还没有重放操作」，不是功能坏了：
#   探针只在真的发生一次取流/封面请求时才写日志。
#
# 第二个参数（曲目 id）怎么拿：在飞牛音乐里打开出问题的推荐歌单，
# 用浏览器开发者工具看 playlist 曲目列表请求的返回，条目里 `id` 字段
# 形如 `lx:wy:<网易songid>` / `lx:tx:<QQ songmid>` / `lx:kg:<hash>`。
# 拿不到也可以不传，脚本其余部分照样有诊断价值。

CONTAINER="${FNMUSIC_CONTAINER:-fnmusic-sources}"
UNIT="${FNMUSIC_UNIT:-fnmusic-ext.service}"
LX_PORT="${FNMUSIC_LX_PORT:-8772}"
OUT="${FNMUSIC_DIAG_OUT:-/tmp/fnmusic-playback-diag-$(date +%Y%m%d-%H%M%S).txt}"
LOG_TAIL="${FNMUSIC_DIAG_LOG_LINES:-5000}"

: > "$OUT" 2>/dev/null || OUT="/dev/null"

say() { printf '%s\n' "$*" | tee -a "$OUT"; }
sec() { say ""; say "===== $* ====="; }

# 仓库根目录（.env 所在处）——代理真正读取的那份配置
REPO_DIR="${FNMUSIC_REPO_DIR:-}"
if [ -z "$REPO_DIR" ]; then
    here="$(cd "$(dirname "$0")/.." 2>/dev/null && pwd)"
    for cand in "$here" \
                /vol1/@appcenter/fnmusic-ext/repo \
                /var/apps/fnmusic-ext/target/repo \
                /var/apps/fnmusic-ext/repo; do
        [ -n "$cand" ] || continue
        if [ -f "$cand/.env" ] || [ -d "$cand/proxy" ]; then REPO_DIR="$cand"; break; fi
    done
fi
ENV_FILE="${FNMUSIC_ENV_FILE:-}"
if [ -z "$ENV_FILE" ] && [ -n "$REPO_DIR" ] && [ -f "$REPO_DIR/.env" ]; then
    ENV_FILE="$REPO_DIR/.env"
fi

env_get() {   # env_get KEY [默认值]  —— 从宿主机 repo/.env 里取值
    [ -n "$ENV_FILE" ] && [ -f "$ENV_FILE" ] || { printf '%s' "${2:-}"; return; }
    v="$(sed -n "s/^[[:space:]]*\(export[[:space:]]\{1,\}\)\?$1[[:space:]]*=[[:space:]]*//p" "$ENV_FILE" \
         | tail -n1 | sed 's/^["'"'"']//; s/["'"'"']$//; s/[[:space:]]*$//')"
    if [ -n "$v" ]; then printf '%s' "$v"; else printf '%s' "${2:-}"; fi
}

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

# 代理日志来源：systemd journal 优先
JOURNAL_MODE=0
if command -v journalctl >/dev/null 2>&1; then
    if journalctl -u "$UNIT" -n 1 --no-pager >/dev/null 2>&1; then JOURNAL_MODE=1; fi
fi

jlog() {   # jlog [grep -E 模式]
    if [ "$JOURNAL_MODE" = "1" ]; then
        if [ -n "${1:-}" ]; then
            journalctl -u "$UNIT" -n "$LOG_TAIL" --no-pager -o short-iso 2>&1 | grep -E "$1"
        else
            journalctl -u "$UNIT" -n "$LOG_TAIL" --no-pager -o short-iso 2>&1
        fi
    elif [ -n "${FNMUSIC_PROXY_LOG:-}" ] && [ -f "$FNMUSIC_PROXY_LOG" ]; then
        if [ -n "${1:-}" ]; then grep -E "$1" "$FNMUSIC_PROXY_LOG"; else cat "$FNMUSIC_PROXY_LOG"; fi
    else
        echo "(该宿主无可用 journalctl，且未设置 FNMUSIC_PROXY_LOG 指向代理日志文件)"
    fi
}

say "fnmusic-ext 播放诊断报告（v2 版：代理日志走 journal，容器日志另列）"
say "生成时间: $(date '+%Y-%m-%d %H:%M:%S %z')"
say "输出文件: $OUT"
say "仓库目录(代理读取 .env 处): ${REPO_DIR:-<未找到>}"
say "代理日志来源: $([ "$JOURNAL_MODE" = 1 ] && echo "journalctl -u $UNIT" || echo "${FNMUSIC_PROXY_LOG:-<无>}")"
say "容器日志来源: $([ "$DOCKER_MODE" = 0 ] && echo "<docker 不可用>" || echo "docker logs $CONTAINER")"

# ------------------------------------------------- 1. 宿主机代理服务与端口 --
sec "1. 宿主机代理服务（fnmusic-ext.service）与端口"
if command -v systemctl >/dev/null 2>&1; then
    say "-- is-active --"
    systemctl is-active "$UNIT" 2>&1 | tee -a "$OUT" || true
    say "-- 关键属性 --"
    systemctl show "$UNIT" --no-pager \
        -p WorkingDirectory -p ExecMainStatus -p NRestarts -p ActiveEnterTimestamp \
        -p SubState 2>&1 | tee -a "$OUT" || true
    say "-- status 摘要 --"
    systemctl status "$UNIT" --no-pager -n 15 2>&1 \
        | sed -n '1,25p' | tee -a "$OUT" || true
else
    say "!! 宿主机无 systemctl（不是 fpk 形态？）"
fi
say ""
say "-- 监听端口（音源容器映射到宿主 127.0.0.1）--"
ports=""
if command -v ss >/dev/null 2>&1; then
    ports="$(ss -ltnp 2>/dev/null | grep -E ':(8768|8770|8772|8774)\b')"
elif command -v netstat >/dev/null 2>&1; then
    ports="$(netstat -ltnp 2>/dev/null | grep -E ':(8768|8770|8772|8774)\b')"
fi
if [ -n "$ports" ]; then
    say "$ports"
else
    say "(没看到 8768/8770/8772/8774 在监听 —— 音源容器可能没起来，或宿主无 ss/netstat)"
fi
say ""
say "解读：代理服务 active + 四个端口都在 = 组件齐全；"
say "      is-active 非 active → 代理根本没起来，一切都是它的连锁反应。"

# --------------------------------------- 2. 宿主机 repo/.env（代理真实配置）--
sec "2. 宿主机 repo/.env —— 代理真正读取的配置"
if [ -n "$ENV_FILE" ] && [ -f "$ENV_FILE" ]; then
    say "配置文件: $ENV_FILE"
    say ""
    say "-- 与本次问题相关的键（值）--"
    grep -E '^[[:space:]]*(export[[:space:]]+)?FNMUSIC_(TEE_SAVE_DIR|TEE_SAVE_ENABLED|TEE_CACHE_MAX|TEE_HANDOFF_MAX|LIBRARY_DIR|LIBRARY_SCAN_PATH|MUSIC_DB|COVER_PROXY|COVER_CACHE_DIR|COVER_CACHE_MAX|COVER_PROBE|STREAM_PROBE|CACHE_DIR|HOME|LX_ENABLED|LX_SOURCES|RECOMMEND_PLAYLIST|AUTO_COVER|LYRIC_AUTO_DL)' \
        "$ENV_FILE" 2>/dev/null | tee -a "$OUT" || true
    say "（上面没出现的键 = 该文件没配，走代码默认值）"
    say ""
    say "-- 该文件里配置过的键名总览（不打印值：可能含账号/token）--"
    grep -oE '^[[:space:]]*(export[[:space:]]+)?[A-Za-z_][A-Za-z0-9_]*[[:space:]]*=' "$ENV_FILE" 2>/dev/null \
        | sed 's/[[:space:]]*=$//; s/^[[:space:]]*//' | sort -u | tr '\n' ' '
    say ""
else
    say "!! 没找到 .env（尝试过：\$FNMUSIC_ENV_FILE、<仓库>/.env、"
    say "   /vol1/@appcenter/fnmusic-ext/repo/.env、/var/apps/fnmusic-ext/target/repo/.env）"
    say "   代理会全部走代码默认值：边听边存开、曲库自动探测、封面代理开。"
fi
say ""
say "注意：容器内执行 docker exec ... env 看不到 FNMUSIC_* 是正常的！"
say "      docker-compose.yml 没有 env_file，容器内的 entrypoint/healthcheck"
say "      是从挂载进来的 /repo/.env 读开关的。别据此判断「配置丢了」。"
say ""
say "想要完整配置快照：代理启动时会打印全部配置项，见第 5 节首行附近"
say "  「=== fnmusic-ext vX.Y.Z configuration ===」段。"

# ------------------------------------------------- 2b. 宿主机曲库与落盘目录 --
sec "2b. 宿主机曲库探测与边听边存落盘目录"
say "-- 飞牛应用数据目录里与 music 相关的项 --"
for vol in /vol1 /vol2 /vol3 /vol4 /vol5; do
    [ -d "$vol/@appdata" ] || continue
    ls -la "$vol/@appdata/" 2>/dev/null | grep -iE 'trim|music' | sed "s|^|$vol/@appdata/ |"
done | tee -a "$OUT" || true
say ""
say "-- 候选 music.db 是否存在 --"
DB_HIT=""
for db in "$(env_get FNMUSIC_MUSIC_DB /usr/local/apps/@appdata/trim.music/db/music.db)" \
          /vol1/@appdata/trim.music/db/music.db \
          /usr/local/apps/@appdata/trim.music/db/music.db; do
    if [ -f "$db" ]; then
        say "[存在] $db"
        [ -n "$DB_HIT" ] || DB_HIT="$db"
    else
        say "[缺失] $db"
    fi
done
say ""
say "-- 共享曲库路径（读 music.db 的 shared_library 表）--"
if [ -n "$DB_HIT" ]; then
    if command -v sqlite3 >/dev/null 2>&1; then
        sqlite3 "file:$DB_HIT?mode=ro" "SELECT path FROM shared_library ORDER BY id;" 2>&1 \
            | tee -a "$OUT" || true
    elif command -v python3 >/dev/null 2>&1; then
        python3 - "$DB_HIT" <<'PYEOF' 2>&1 | tee -a "$OUT" || true
import sqlite3, sys, os
db = sys.argv[1]
try:
    con = sqlite3.connect("file:%s?mode=ro" % db, uri=True)
    rows = [r[0] for r in con.execute("SELECT path FROM shared_library ORDER BY id")]
    con.close()
except Exception as e:
    print("读取失败: %s" % e)
    raise SystemExit(1)
if not rows:
    print("(shared_library 表为空 —— 飞牛音乐里没有共享曲库)")
for p in rows:
    print("%s  %s" % ("[可用]" if os.path.isdir(p) else "[不可用]", p))
PYEOF
    else
        say "(宿主无 sqlite3 也无 python3，按第 5 节代理日志里的告警为准)"
    fi
else
    say "!! 三个候选 music.db 都不存在 —— 代理探测必失败，边听边存会退化到"
    say "   <仓库>/cache（文件名是哈希、不进官方曲库）＝「边听边存失效」。"
    say "   修法：在 ${ENV_FILE:-${REPO_DIR:-<仓库>}/.env} 里加一行"
    say "         FNMUSIC_MUSIC_DB=<真实的 music.db 绝对路径>"
    say "         （或 FNMUSIC_LIBRARY_DIR=<飞牛音乐共享曲库目录>），然后重启应用。"
fi
say ""
say "-- 边听边存落盘候选目录（宿主机侧）--"
TEE_CFG="$(env_get FNMUSIC_TEE_SAVE_DIR)"
LIB_CFG="$(env_get FNMUSIC_LIBRARY_DIR)"
CACHE_CFG="$(env_get FNMUSIC_CACHE_DIR)"
for dir in "$TEE_CFG" "$LIB_CFG" "$CACHE_CFG" \
           "$([ -n "$REPO_DIR" ] && echo "$REPO_DIR/cache")" \
           /vol1/@appcenter/fnmusic-ext/repo/cache; do
    [ -n "$dir" ] || continue
    [ -d "$dir" ] || { continue; }
    say "[$dir]  ($(find "$dir" -maxdepth 1 -type f 2>/dev/null | wc -l) 个文件)"
    ls -lt "$dir" 2>/dev/null | head -12 | sed 's/^/    /' | tee -a "$OUT"
done
say "（没有任何目录列出来 = 这些怀疑的地方都不存在，见第 5 节的 tee finalize 行看真实落点）"
say ""
say "-- 封面代理缓存目录 --"
for croot in "$(env_get FNMUSIC_COVER_CACHE_DIR)" \
              "$([ -n "$CACHE_CFG" ] && echo "$CACHE_CFG/covers")" \
              "$([ -n "$REPO_DIR" ] && echo "$REPO_DIR/cache/covers")"; do
    [ -n "$croot" ] || continue
    if [ -d "$croot" ]; then
        say "[$croot] 共 $(ls "$croot" 2>/dev/null | wc -l) 个文件，占用 $(du -sh "$croot" 2>/dev/null | cut -f1)"
        ls -lt "$croot" 2>/dev/null | head -6 | sed 's/^/    /' | tee -a "$OUT"
    else
        say "(不存在) $croot"
    fi
done
say "（2.7.7+ 首次成功代理封面时才创建该目录；一直不存在 = 一次封面代理都没成功）"

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

# --------------------------------------------------- 5. 代理日志（journal）--
sec "5. 代理日志 journalctl -u $UNIT（取流 / 边听边存 / 封面都在这里）"
if [ "$JOURNAL_MODE" != "1" ]; then
    say "!! 拿不到 journal（无 journalctl 或权限不足，请用 sudo 跑）。"
    say "   可先单独执行：sudo journalctl -u $UNIT -n 200 --no-pager"
fi

say ""
say "-- 5a. 启动配置快照与曲库自检 --"
jlog 'fnmusic-ext v[0-9]|configuration|\[自检\]|曲库|落盘目录|llm_enabled' \
    | tail -60 | tee -a "$OUT" || true
say ""
say "  关键两行（2.7.9+）："
say "    [自检] 曲库目录 = <路径>"
say "    [自检] 边听边存落盘目录 = <路径>"
say "    → 边听边存的文件就落在第二个路径下；它等于 cache 目录就说明曲库探测失败。"
say "    同段还会打「曲库自动探测命中：music.db=...」说明是按宿主机布局兜底找到的。"

say ""
say "-- 5b. 边听边存（tee）--"
jlog 'stream probe|tee |finalize|落盘|full fetch|handoff|continuation|曲库探测失败' \
    | tail -80 | tee -a "$OUT" || true
say ""
say "  关注这几行："
say "    stream probe: ... range=None / range='bytes=0-'  → 本次取流符合落盘条件"
say "    stream probe: ... tee_eligible=True              → 落盘资格判定通过"
say "    stream probe: ... tee_eligible=False             → 定长窗口取流，tee 永不触发"
say "    tee finalize done for <guid>: dest=<路径>        → 落盘成功，看 dest 在哪"
say "    曲库探测失败：music.db 不存在 ... / shared_library 无可用路径"
say "                                                     → 落盘退化到 cache（哈希名）"
say "    本段为空 = 这段时间内没有任何在线曲目播放过（探针只在取流时写）。"

say ""
say "-- 5c. 封面代理（cover probe）--"
jlog 'cover probe|playlist cover|cover cache|封面' | tail -60 | tee -a "$OUT" || true
say ""
say "  关注这几行："
say "    cover probe: GET coverId=<歌单guid> ... -> 200 local-file   → 命中 NAS 缓存（正常）"
say "    cover probe: ... -> 302 redirect-><CDN>                     → 代理没取到，回落直链"
say "    cover probe: ... -> 200 inline-image(占位图...)             → 走了占位图兜底"
say "    playlist cover <guid> kind=daily ... -> placeholder(no usable cover)"
say "       → 该歌单快照整单没有可用封面（在线音源构建的歌单常见；2.7.8+ 已会兜底）"
say "    netease playlist cover ... -> placeholder(no registered cover)"
say "       → 网易歌单封面直链未登记（重启后反查表未重建）"

say ""
say "-- 5d. 其他告警（近 $LOG_TAIL 行里的 WARNING/ERROR/Traceback）--"
jlog 'WARNING|ERROR|Traceback|Exception|异常' | tail -50 | tee -a "$OUT" || true

# --------------------------------------------------- 6. 容器日志（docker）--
sec "6. 容器 fnmusic-sources：状态与音源日志（★ 这里不会有 tee/cover probe）"
if [ "$DOCKER_MODE" = "0" ]; then
    say "!! 无法连通 Docker daemon（或宿主无 docker），请用 sudo 运行，"
    say "   或把当前用户加入 docker 组。本节的音源侧日志与容器状态已跳过。"
else
d ps -a --filter "name=^/${CONTAINER}$" \
    --format 'name={{.Names}}  status={{.Status}}  image={{.Image}}' 2>&1 | tee -a "$OUT" || true

if [ -z "$(d ps -q --filter "name=^/${CONTAINER}$" 2>/dev/null)" ]; then
    say "!! 容器 $CONTAINER 不在运行中，音源侧日志不可用。"
else
    say ""
    say "-- supervisor 各程序状态 --"
    d exec "$CONTAINER" supervisorctl status 2>&1 | tee -a "$OUT" || true
    say ""
    say "-- /repo/.env 里的音源开关（容器内 entrypoint 读的就是这个文件）--"
    d exec "$CONTAINER" sh -c 'grep -E "^[[:space:]]*FNMUSIC_(LX|MUSICDL|NETEASE|WEBUI)_ENABLED" /repo/.env 2>/dev/null || echo "(读不到 /repo/.env)"' \
        2>&1 | tee -a "$OUT" || true
    say ""
    say "-- 容器内落盘目录 --"
    d exec "$CONTAINER" sh -c '
      for dir in /data/cache /data/cache/covers /data/lxserver; do
        [ -d "$dir" ] || { echo "(不存在) $dir"; continue; }
        echo "[$dir] $(ls "$dir" 2>/dev/null | wc -l) 项"
      done' 2>&1 | tee -a "$OUT" || true
    say ""
    say "-- 音源/解析/转码/迁移 相关日志 --"
    d logs --tail 3000 "$CONTAINER" 2>&1 \
        | grep -Ei 'lx|resolve|track/url|transcode|ffmpeg|迁移|迁移异常|source|激活|supervisor|error|warn|failed|异常' \
        | tail -120 | tee -a "$OUT" || true
fi
fi

# --------------------------------------------------- 7. 转码 HLS 一致性 --
sec "7. 转码 HLS 会话一致性（声明分片数 vs 磁盘实际分片数）"
if [ "$DOCKER_MODE" != "0" ]; then
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
      done' 2>&1 | tail -60 | tee -a "$OUT" || true
else
    say "（docker 不可用，跳过）"
fi

# ------------------------------------------------------------ 8. 资源 --
sec "8. 转码进程、负载与磁盘"
if [ "$DOCKER_MODE" != "0" ]; then
    d exec "$CONTAINER" sh -c 'ps -eo pcpu,pmem,etime,args 2>/dev/null | grep ffmpeg | grep -v grep' \
        2>&1 | tail -15 | tee -a "$OUT" || true
    say "（ffmpeg 常驻多个且 CPU 打满时，标准音质会断续；与本文档主线无关但值得留意）"
    d exec "$CONTAINER" sh -c 'cat /proc/loadavg 2>/dev/null' 2>&1 | tee -a "$OUT" || true
    d exec "$CONTAINER" sh -c 'df -h "${FNMUSIC_CACHE_DIR:-/data}" 2>/dev/null' 2>&1 | tee -a "$OUT" || true
else
    say "（docker 不可用，跳过）"
fi
say ""
say "-- 宿主机根盘与数据卷 --"
df -h / /vol1 2>/dev/null | tee -a "$OUT" || true

# ------------------------------------------------------------ 结论速查 --
sec "9. 怎么读这份报告（按症状对号入座）"
say "A. 一点播放就跳下一首 / 歌不出声："
say "   第 1 节服务或端口缺失 → 代理/音源没起来；"
say "   第 3 节 active=false     → 洛雪源未激活；"
say "   第 4 节三档全 fail       → 源脚本解析不出直链；"
say "   第 5d 节 stream 取流失败 → 候选直链全不可用。"
say ""
say "B. 边听边存失效 / 不知道存哪了："
say "   第 5a 节「[自检] 边听边存落盘目录」= 真实落点（这是 2.7.9+ 才有的行）；"
say "   它等于 cache 目录 → 第 2b 节里三个 music.db 候选都不存在，按该节提示配 FNMUSIC_MUSIC_DB；"
say "   第 5b 节完全没有 stream probe 行 → 这段时间没有在线曲目播放过（先在浏览器里放一首歌再重跑）。"
say ""
say "C. 列表/歌单没有封面："
say "   第 5c 节有 cover probe 行 → 按最后一列（local-file / 302 / inline-image）判断；"
say "   第 5c 节完全为空 → 升级后没有打开过任何列表页（探针只在真的请求封面时写）；"
say "   大量 playlist cover ... -> placeholder → 该歌单快照没封面来源，把这段贴回对话。"
say ""
say "★ 重要：升级 fpk 后请先「播一首在线歌 + 打开一个歌单列表」再跑本脚本，"
say "  否则所有探针段都是空的，无法判断新版本修好了没有。"
say ""
say "===== 报告结束 ====="
say "报告文件: $OUT"
say "把该文件内容贴回对话即可（脚本全程只读）。"
