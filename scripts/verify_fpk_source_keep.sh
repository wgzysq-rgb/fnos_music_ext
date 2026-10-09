#!/usr/bin/env bash
# fpk 生命周期校验：应用中心重装/升级后必须保住用户已安装的音源。
#
# 用法：bash scripts/verify_fpk_source_keep.sh    （只读校验，不触碰真实
# /var/lib、/etc、/root 与宿主 docker/systemd；全部落到 /tmp 沙箱）
#
# 为什么另起一个脚本而不并入 packaging/tests/test_fpk_cmds.py：那套 pytest 沙箱靠
# os.symlink 把真实工具暴露进 PATH，在 Windows 上未开 SeCreateSymbolicLinkPrivilege
# 会 WinError 1314，整个文件连 fixture 都建不起来；而 packaging/tests/test_fpk_pack.py
# 依赖 build.sh（需要 rsync）。本脚本不依赖任何特权，改为「把桩目录前置到既有 PATH」，
# 只桩 docker / rm 两个命令，因此在 Windows（Git Bash）与 Linux 上都能跑。
# rm 必须桩掉：某些环境下真实 rm 被包装成回收站转发，非交互场景会阻塞。
set -uo pipefail

# 本脚本位于 <repo>/scripts，仓库根在上一级
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CMD="$ROOT/packaging/fpk/cmd"
# 必须用 POSIX 风格工作目录：Windows 上 mktemp -d 返回 "C:\...\Temp\tmp.xxx"，
# 反斜杠+盘符混进 PATH 后 bash 解析不到 $WORK/bin，桩会全部失效。
WORK="/tmp/fnmusic-verify-$$"
mkdir -p "$WORK"

mkdir -p "$WORK/bin" "$WORK/appdest/repo" "$WORK/pkgvar" "$WORK/logs"

cat > "$WORK/bin/docker" <<'EOF'
#!/bin/bash
exit 0
EOF

cat > "$WORK/bin/rm" <<'EOF'
#!/bin/bash
# 记录删除请求并真正清空目标（足以验证「脚本发出了删除」这一行为）
for a in "$@"; do
    case "$a" in -*) continue ;; esac
    printf 'rm %s\n' "$a" >> "${STUB_RM_LOG:-/dev/null}"
    if [ -e "$a" ]; then : > "$a" 2>/dev/null || true; fi
done
exit 0
EOF
chmod +x "$WORK/bin/docker" "$WORK/bin/rm"

cat > "$WORK/appdest/repo/install.sh" <<'EOF'
#!/bin/bash
printf '%s\n' "$*" >> "${STUB_INSTALL_LOG}"
exit "${STUB_INSTALL_RC:-0}"
EOF
chmod +x "$WORK/appdest/repo/install.sh"

FAILED=0
SANDBOX_PATH="$WORK/bin:/usr/bin:/bin"
SNAPSHOT="$WORK/state/installed-source"

base_env() {
    env -i \
        PATH="$SANDBOX_PATH" \
        TRIM_APPDEST="$WORK/appdest" \
        TRIM_PKGVAR="$WORK/pkgvar" \
        TRIM_TEMP_LOGFILE="$WORK/logs/trim.txt" \
        STUB_INSTALL_LOG="$WORK/logs/install.log" \
        STUB_RM_LOG="$WORK/logs/rm.log" \
        FNMUSIC_DURABLE_LOG="$WORK/logs/durable.log" \
        FNMUSIC_UNIT_FILE="$WORK/absent-unit.service" \
        FNMUSIC_DEPLOYMENT_FILE="$WORK/absent-deployment" \
        FNMUSIC_SOURCE_SNAPSHOT="$SNAPSHOT" \
        "$@"
}

check() {
    if [ "$2" = "$3" ]; then
        printf '  ok    %-52s -> %s\n' "$1" "$2"
    else
        printf '  FAIL  %-52s -> %s (want %s)\n' "$1" "$2" "$3"
        FAILED=1
    fi
}

blank() { : > "$1"; }

# ---- _common 读取 .env（真实格式是单引号包裹） --------------------------------
env_common() {
    printf '%b' "${ENVTEXT:-}" > "$WORK/appdest/repo/.env"
    base_env bash -c "SELF_DIR='$CMD'; . '$CMD/_common'; $1"
}

echo "== _common 读 .env（install.sh / WebUI 写出的是 KEY='true'）=="
ENVTEXT="FNMUSIC_LX_ENABLED='true'\nFNMUSIC_MUSICDL_ENABLED='false'\n"
check "saved_source: 单引号 KEY='true'" "$(env_common fnmusic_saved_source)" "lxmusic"
ENVTEXT='export FNMUSIC_MUSICDL_ENABLED="true"\n'
check "saved_source: export + 双引号" "$(env_common fnmusic_saved_source)" "musicdl"
ENVTEXT="FNMUSIC_NETEASE_ENABLED='yes'\n"
check "saved_source: 'yes' 视为启用" "$(env_common fnmusic_saved_source)" "musicbox"
ENVTEXT="FNMUSIC_LX_ENABLED='TRUE'\r\n"
check "saved_source: 大写 + CRLF 行尾" "$(env_common fnmusic_saved_source)" "lxmusic"
ENVTEXT="FNMUSIC_MUSICDL_ENABLED=true\n"
check "saved_source: 裸值（历史写法）" "$(env_common fnmusic_saved_source)" "musicdl"
ENVTEXT="FNMUSIC_LX_ENABLED='false'\n"
check "saved_source: 关闭 -> 空" "$(env_common fnmusic_saved_source)" ""

ENVTEXT="LX_SOURCE_URL='http://s/x.js'\n"
check "saved_lx_url: 剥掉单引号" "$(env_common fnmusic_saved_lx_url)" "http://s/x.js"
ENVTEXT="LX_SOURCE_URL='http://old/x.js'\nLX_SOURCE_URL='http://new/y.js'\n"
check "saved_lx_url: 重复键取最后一行" "$(env_common fnmusic_saved_lx_url)" "http://new/y.js"

mkdir -p "$WORK/state"
ENVTEXT="FNMUSIC_MUSICDL_ENABLED='true'\n"
printf 'lxmusic\n' > "$SNAPSHOT"
check "snapshot_source: .env 优先于快照" "$(env_common fnmusic_snapshot_source)" "musicdl"
ENVTEXT=""
check "snapshot_source: .env 无记录时用快照" "$(env_common fnmusic_snapshot_source)" "lxmusic"
printf 'bogus\n' > "$SNAPSHOT"
check "snapshot_source: 快照非法 -> 空" "$(env_common fnmusic_snapshot_source)" ""
blank "$SNAPSHOT"
check "snapshot_source: 两者皆无 -> 空" "$(env_common fnmusic_snapshot_source)" ""

# ---- install_callback 组参数 -------------------------------------------------
run_cb() {
    blank "$WORK/logs/install.log"
    base_env "$@" bash "$CMD/install_callback" >/dev/null 2>&1
    tail -n1 "$WORK/logs/install.log" 2>/dev/null
}

echo
echo "== install_callback：重装必须沿用已安装音源 =="
ENV_LX="FNMUSIC_LX_ENABLED='true'\nFNMUSIC_MUSICDL_ENABLED='false'\nLX_SOURCE_URL='http://s/lx.js'\n"
printf '%b' "$ENV_LX" > "$WORK/appdest/repo/.env"
blank "$SNAPSHOT"
check "已装洛雪源 + 向导默认提交 musicdl" \
    "$(run_cb wizard_sources=musicdl wizard_extend=true)" \
    "--non-interactive --sources lxmusic --webui --adopt --lx-source-url http://s/lx.js --lx-skip-verify --extend"
if grep -q musicdl "$WORK/logs/durable.log" && grep -q lxmusic "$WORK/logs/durable.log"; then
    echo "  ok    持久日志留痕了「向导选择 / 实际沿用」"
else
    echo "  FAIL  持久日志缺留痕"; FAILED=1
fi

blank "$WORK/appdest/repo/.env"          # 模拟 .env 已被重装流程清空
printf 'lxmusic\n' > "$SNAPSHOT"         # 只剩 install_init 留下的快照
blank "$WORK/logs/durable.log"
check "repo/.env 已被清空 -> 用快照兜底" "$(run_cb wizard_sources=musicdl)" \
    "--non-interactive --sources lxmusic --webui --adopt --lx-skip-verify --extend"

echo
echo "== install_callback：首次安装仍按向导 =="
blank "$WORK/appdest/repo/.env"
blank "$SNAPSHOT"
check "全新安装 musicbox" "$(run_cb wizard_sources=musicbox)" \
    "--non-interactive --sources musicbox --webui --adopt --extend"
check "全新安装 lxmusic + 向导 URL（不跳过校验）" \
    "$(run_cb wizard_sources=lxmusic wizard_lx_url=http://s/y.js)" \
    "--non-interactive --sources lxmusic --webui --adopt --lx-source-url http://s/y.js --extend"
check "全新安装 + 无效向导值 -> musicdl" "$(run_cb wizard_sources=bogus)" \
    "--non-interactive --sources musicdl --webui --adopt --extend"

# ---- install_init 写快照 / uninstall_callback 清快照 ------------------------
echo
echo "== 快照生命周期 =="
printf "FNMUSIC_LX_ENABLED='true'\n" > "$WORK/appdest/repo/.env"
blank "$SNAPSHOT"
# 变体脚本放在 $WORK 下，必须把 SELF_DIR 指回真实 cmd 目录，否则 . _common 找不到；
# 另把两项硬预检换成一个恒真条件（断言链结构不变）。真实 socket 判定由
# packaging/tests/test_fpk_cmds.py 覆盖。
sed -e "s#^SELF_DIR=.*#SELF_DIR=\"$CMD\"#" \
    -e "s#\[ -S /var/run/trim_music.socket \]#[ -d \"$WORK/appdest\" ]#" \
    -e "s#\[ -S /var/run/trim_music_upstream.socket \]#[ -d \"$WORK/appdest\" ]#" \
    "$CMD/install_init" > "$WORK/init_variant"
base_env bash "$WORK/init_variant" >/dev/null 2>&1
check "install_init 写出快照" "$(tr -d '[:space:]' < "$SNAPSHOT" 2>/dev/null)" "lxmusic"

printf "FNMUSIC_LX_ENABLED='false'\n" > "$WORK/appdest/repo/.env"
blank "$SNAPSHOT"
base_env bash "$WORK/init_variant" >/dev/null 2>&1
check "未配置音源时不写快照" "$(tr -d '[:space:]' < "$SNAPSHOT")" ""

printf 'lxmusic\n' > "$SNAPSHOT"
blank "$WORK/logs/rm.log"
base_env bash "$CMD/uninstall_callback" >/dev/null 2>&1
RM_HITS="$(grep -c 'installed-source' "$WORK/logs/rm.log" || true)"
check "uninstall_callback 请求删除快照" "${RM_HITS:-0}" "1"
check "uninstall_callback 清空快照内容" "$(tr -d '[:space:]' < "$SNAPSHOT" 2>/dev/null)" ""

echo
if [ "$FAILED" -eq 0 ]; then echo "全部通过"; else echo "存在失败项"; fi
exit "$FAILED"
