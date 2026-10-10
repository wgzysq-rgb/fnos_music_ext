#!/bin/bash
# fnmusic-ext 安装失败诊断采集（只读，不修改任何系统状态、不输出任何密钥）
# 用法: sudo bash /var/apps/fnmusic-ext/target/repo/scripts/diagnose_install.sh
#       （target 是软链 → /vol1/@appcenter/fnmusic-ext；git clone 安装则为 <仓库>/scripts/）
# 输出：终端打印 + 生成 /tmp/fnmusic-diag-<时间戳>.txt
# 把该文件内容贴回即可。

set -u

OUT="/tmp/fnmusic-diag-$(date +%Y%m%d-%H%M%S).txt"
: > "${OUT}"

sec() { printf '\n===== %s =====\n' "$*" | tee -a "${OUT}"; }
run() { printf '$ %s\n' "$*" >> "${OUT}"; "$@" >> "${OUT}" 2>&1 || true; }

{
    echo "fnmusic-ext 安装诊断报告"
    echo "生成时间: $(date -Iseconds)"
    echo "主机: $(uname -a)"
} | tee -a "${OUT}"

# ---------- 1. 安装日志（最关键：真正的失败原因在这里） ----------
sec "1. 安装日志 /var/log/apps/fnmusic-ext-install.log（末 200 行）"
if [ -f /var/log/apps/fnmusic-ext-install.log ]; then
    tail -200 /var/log/apps/fnmusic-ext-install.log | tee -a "${OUT}"
else
    echo "(日志文件不存在)" | tee -a "${OUT}"
    ls -la /var/log/apps/ 2>&1 | tee -a "${OUT}"
fi

# ---------- 2. Docker 基本状态 ----------
sec "2. Docker 版本 / daemon 状态"
run docker version
run docker info
sec "2b. Docker daemon.json（镜像加速器 + DNS + 代理配置）"
if [ -f /etc/docker/daemon.json ]; then
    cat /etc/docker/daemon.json | tee -a "${OUT}"
else
    echo "(无 /etc/docker/daemon.json)" | tee -a "${OUT}"
fi

# ---------- 3. 宿主机 DNS 与网络 ----------
sec "3. 宿主机 /etc/resolv.conf"
cat /etc/resolv.conf 2>&1 | tee -a "${OUT}"
sec "3b. 关键域名解析测试"
for d in mirrors.tencent.com pypi.org deb.debian.org registry-1.docker.io docker.1ms.run docker.m.daocloud.io ghfast.top; do
    printf '%-28s -> ' "${d}" | tee -a "${OUT}"
    getent hosts "${d}" 2>/dev/null | head -1 | tee -a "${OUT}" || echo "解析失败" | tee -a "${OUT}"
done

sec "3c. 宿主机直连测试（curl 头 15s 超时）"
for u in \
    "https://mirrors.tencent.com/pypi/simple/fastapi/" \
    "http://deb.debian.org/debian/dists/bookworm/Release" \
    "https://registry-1.docker.io/v2/" \
    "https://ghfast.top/https://github.com/XCQ0607/lxserver/releases/download/v2.1.2/lx-music-sync-server-v2.1.2-server.zip" ; do
    printf '%-100s ' "${u}" >> "${OUT}"
    curl -sS -o /dev/null -m 15 -w 'code=%{http_code} time=%{time_total}\n' "${u}" >> "${OUT}" 2>&1 || echo 'FAILED' >> "${OUT}"
done
tail -6 "${OUT}"

# ---------- 4. 构建容器内网络（模拟 Dockerfile 的 RUN 环境） ----------
sec "4. 容器内网络测试（busybox，验证构建网络链路）"
if docker image inspect busybox >/dev/null 2>&1 || docker pull --quiet busybox >/dev/null 2>&1; then
    run docker run --rm --network host busybox sh -c '
        echo "--- resolv.conf ---"; cat /etc/resolv.conf
        echo "--- 解析 ---"
        nslookup mirrors.tencent.com 2>&1 | head -5
        echo "--- 连通 ---"
        wget -q -T 10 -O - http://deb.debian.org/debian/dists/bookworm/Release 2>&1 | head -c 200; echo
    '
else
    echo "(busybox 拉取失败——说明 Docker 拉镜像链路本身就不通)" | tee -a "${OUT}"
fi

# ---------- 5. 已有镜像与容器状态 ----------
sec "5. 现有镜像 / 容器"
run docker images
run docker ps -a
sec "5b. 是否已存在半成品镜像"
run docker image inspect fnmusic-sources:latest

# ---------- 6. 构建缓存与磁盘 ----------
sec "6. 磁盘空间"
run df -h / /var/lib/docker
sec "6b. 当前 .env 中的基础镜像记录"
# 仓库载荷落点随 fnOS 版本不同（<app>/target/repo 或 <app>/repo），逐个探测而不是硬编码
ENV_FILE=""
for _cand in /var/apps/fnmusic-ext/target/repo/.env /var/apps/fnmusic-ext/repo/.env; do
    if [ -f "${_cand}" ]; then ENV_FILE="${_cand}"; break; fi
done
if [ -n "${ENV_FILE}" ]; then
    echo ".env: ${ENV_FILE}" | tee -a "${OUT}"
    grep -E '^FNMUSIC_(BASE_IMAGE|PIP_INDEX|APT_MIRROR)=' "${ENV_FILE}" | tee -a "${OUT}"
else
    echo "(未找到 repo/.env；git clone 方式安装时仓库目录由你自定)" | tee -a "${OUT}"
fi

sec "诊断完成"
{
    echo
    echo "报告文件: ${OUT}"
    echo "请把上面内容（或该文件）完整贴回。"
    echo "注：本脚本只读，不含任何账号密码或密钥。"
} | tee -a "${OUT}"
