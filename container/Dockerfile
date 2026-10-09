# fnmusic-sources 统一镜像：musicdl + musicbox + lxmusic + webui 四进程（supervisor 管理，按需启动）
# 基础镜像引用可由 build args 覆盖（默认官方源；国内网络自动回退见根目录 ensure_base_image.sh）
ARG BASE_IMAGE=python:3.13-slim
FROM ${BASE_IMAGE}

ARG PIP_INDEX_URL=https://mirrors.tencent.com/pypi/simple/
ARG APT_MIRROR=https://mirrors.tencent.com
# 备用源链：留空即用下列内置默认（构建层 shell 兜底；不要在 compose 里以空值透传）
ARG PIP_FALLBACK_INDEXES=
ARG APT_FALLBACK_MIRRORS=

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_INDEX_URL=${PIP_INDEX_URL} \
    XDG_DATA_HOME=/data \
    XDG_CACHE_HOME=/data/cache \
    XDG_CONFIG_HOME=/data/config \
    LX_DATA_DIR=/data/lxmusic \
    WEBUI_REPO_DIR=/repo

WORKDIR /srv

# nodejs（洛雪自定义源 Node 沙箱，trixie 自带 20.x）/ ffmpeg（musicdl 转封装）/ supervisor
# apt 源默认国内镜像（与 pip 同策略，APT_MIRROR 可覆盖），仅在本构建层内临时替换。
# 构建容器无法复用"指向本机"的宿主 DNS（Docker 会剔除 127.x 并回退 8.8.8.8，国内不可达），
# 因此不探测、始终把国内可达的公共 DNS 写到最前（写入失败不中断）；apt-get update
# 对 DNS 失败只报 W: 警告且退出码为 0，回退判定看「索引是否真正落地」而非退出码；
# 多级源链（APT_MIRROR → APT_FALLBACK_MIRRORS → 官方 deb.debian.org）都取不到索引才判失败。
# 安装完成后恢复官方源，宿主机与最终镜像不受影响。
# 失败文案用 "ERROR:" 不用 "[ERROR]"：后者会被应用中心弹窗 grep 成整段 RUN 标题。
RUN set -eu; \
    fix_dns() { \
        { \
            printf 'nameserver 223.5.5.5\nnameserver 119.29.29.29\n'; \
            grep -vE '^[[:space:]]*nameserver[[:space:]]+(127\.|::1|localhost)' /etc/resolv.conf 2>/dev/null || true; \
        } > /tmp/resolv.fnmusic 2>/dev/null || true; \
        cat /tmp/resolv.fnmusic > /etc/resolv.conf 2>/dev/null || true; \
    }; \
    swap_apt_host() { \
        _m="${1%/}"; \
        for f in /etc/apt/sources.list.d/debian.sources; do \
            [ -f "${f}" ] && sed -i -E "s#^(URIs:[[:space:]]*).*#\1${_m}/debian#" "${f}" || true; \
        done; \
        if [ -f /etc/apt/sources.list ]; then \
            sed -i -E "s#^deb[[:space:]]+https?://[^[:space:]]+[[:space:]]+#deb ${_m}/debian #" /etc/apt/sources.list || true; \
        fi; \
    }; \
    apt_update() { apt-get update -o Acquire::Retries=1 -o Acquire::http::Timeout=15 -o Acquire::https::Timeout=15 || true; }; \
    lists_fetched() { [ -n "$(find /var/lib/apt/lists -name '*Packages*' -print -quit 2>/dev/null || true)" ]; }; \
    fetched=0; \
    _mir_chain="${APT_MIRROR:-https://mirrors.tencent.com} ${APT_FALLBACK_MIRRORS:-https://mirrors.aliyun.com https://mirrors.tuna.tsinghua.edu.cn}"; \
    for _mir in ${_mir_chain}; do \
        [ -n "${_mir}" ] || continue; \
        echo "[INFO] apt 源尝试: ${_mir}"; \
        fix_dns; \
        rm -rf /var/lib/apt/lists/*; \
        swap_apt_host "${_mir}"; \
        apt_update; \
        if lists_fetched; then fetched=1; break; fi; \
        echo "[WARN] apt 源 ${_mir} 未取到索引，切换下一源..." >&2; \
    done; \
    if [ "${fetched}" -ne 1 ]; then \
        echo "[WARN] 国内 apt 镜像均未取到索引，回退官方源 deb.debian.org 重试..." >&2; \
        fix_dns; \
        rm -rf /var/lib/apt/lists/*; \
        swap_apt_host 'http://deb.debian.org'; \
        apt_update; \
    fi; \
    if ! lists_fetched; then \
        echo "ERROR: apt 镜像源与官方源均未取到索引：构建容器无法访问 Debian 源。" >&2; \
        echo "ERROR: 若宿主机 /etc/resolv.conf 的 DNS 指向本机（127.x），Docker 容器无法复用，" >&2; \
        echo "ERROR: 可在 Docker daemon.json 配置 \"dns\": [\"223.5.5.5\"] 并重启 Docker 后重试。" >&2; \
        exit 1; \
    fi; \
    apt-get install -y --no-install-recommends nodejs ffmpeg supervisor ca-certificates unzip curl; \
    swap_apt_host 'http://deb.debian.org'; \
    rm -rf /var/lib/apt/lists/*

# 四个服务的 Python 依赖并集（各自 requirements 独立演进，镜像统一安装）
# 构建容器 DNS 失败自愈同 apt 步骤（resolv.conf 改写不跨 RUN 层持久，需每步自带）；
# pip 源多级回退：主源(PIP_INDEX_URL) → PIP_FALLBACK_INDEXES 依次重试 → 官方 PyPI 沉底。
# 单次超时/重试显式收紧，避免弱网下每源耗满默认 15s×5retry 把安装卡死。
COPY musicdl-service/requirements.txt /srv/req.musicdl
COPY musicbox-service/requirements.txt /srv/req.musicbox
COPY lxmusic-service/requirements.txt /srv/req.lxmusic
COPY webui-service/requirements.txt /srv/req.webui
RUN set -eu; \
    fix_dns() { \
        { \
            printf 'nameserver 223.5.5.5\nnameserver 119.29.29.29\n'; \
            grep -vE '^[[:space:]]*nameserver[[:space:]]+(127\.|::1|localhost)' /etc/resolv.conf 2>/dev/null || true; \
        } > /tmp/resolv.fnmusic 2>/dev/null || true; \
        cat /tmp/resolv.fnmusic > /etc/resolv.conf 2>/dev/null || true; \
    }; \
    fix_dns; \
    REQS="-r /srv/req.musicdl -r /srv/req.musicbox -r /srv/req.lxmusic -r /srv/req.webui"; \
    PIP_OPTS="--no-cache-dir --timeout 30 --retries 2"; \
    _idx_chain="${PIP_INDEX_URL:-https://mirrors.tencent.com/pypi/simple/} ${PIP_FALLBACK_INDEXES:-https://mirrors.aliyun.com/pypi/simple/ https://pypi.tuna.tsinghua.edu.cn/simple/ https://mirrors.cloud.tencent.com/pypi/simple/ https://pypi.org/simple}"; \
    ok=0; \
    for _idx in ${_idx_chain}; do \
        [ -n "${_idx}" ] || continue; \
        echo "[INFO] pip 安装尝试源: ${_idx}"; \
        if pip install ${PIP_OPTS} -i "${_idx}" ${REQS}; then ok=1; break; fi; \
        echo "[WARN] pip 源 ${_idx} 安装失败，切换下一源..." >&2; \
    done; \
    if [ "${ok}" -ne 1 ]; then \
        echo "ERROR: 所有 pip 源均安装失败（含官方 PyPI）。国内网络受限时请配置 Docker 代理后重试。" >&2; \
        echo "ERROR: 可在 Docker daemon.json 配置 proxies 段并重启 Docker，或设置 FNMUSIC_PIP_INDEX 指定可达源。" >&2; \
        exit 1; \
    fi; \
    rm -f /srv/req.*

RUN useradd -m -u 1000 appuser \
    && mkdir -p /data/cache /data/config /data/lxmusic /data/lxserver \
    && chown -R appuser:appuser /data

# lxserver 预编译包引入：构建上下文优先（预置离线 zip）+ 多镜像加速回退链
# 注意：下载链用「空串元素代表官方源」表达，不用 \"\" 之类转义引号——
# 那些写法在 compose 变量展开 / BuildKit 引号处理下会被破坏（曾导致 URL
# 被截断成 .../download/v2.1.2 触发 curl Malformed input）。
ARG LXSERVER_MIRROR="https://ghfast.top"
COPY container/lxserver.version /srv/lxserver.version
COPY container/lxserver-artifact/ /srv/lxserver-artifact/
RUN set -eu; \
    . /srv/lxserver.version; \
    TARGET_ZIP="/srv/lxserver-artifact/${LXSERVER_ZIP_NAME}"; \
    if [ ! -s "${TARGET_ZIP}" ]; then \
        echo "[INFO] 预置包不存在或为空，通过镜像下载 ${LXSERVER_ZIP_NAME}..."; \
        REL="https://github.com/XCQ0607/lxserver/releases/download/${LXSERVER_TAG}/${LXSERVER_ZIP_NAME}"; \
        _mirrors="${LXSERVER_MIRRORS:-${LXSERVER_MIRROR:-https://ghfast.top} https://gh-proxy.com https://github.moeyy.xyz}"; \
        DOWNLOAD_OK=0; \
        try_url() { \
            echo "[INFO] 尝试从 $1 下载..."; \
            if curl -fSL --connect-timeout 10 --max-time 180 "$1" -o "${TARGET_ZIP}" && [ -s "${TARGET_ZIP}" ]; then \
                DOWNLOAD_OK=1; return 0; \
            fi; \
            return 1; \
        }; \
        for mirror in ${_mirrors}; do \
            [ -n "${mirror}" ] || continue; \
            try_url "${mirror}/${REL}" && break; \
        done; \
        if [ "${DOWNLOAD_OK}" -ne 1 ]; then \
            echo "[INFO] 镜像加速均不可用，直连 GitHub 重试..."; \
            try_url "${REL}" || true; \
        fi; \
        if [ "${DOWNLOAD_OK}" -ne 1 ]; then \
            echo "ERROR: lxserver release zip 下载失败。离线安装请将 ${LXSERVER_ZIP_NAME}" >&2; \
            echo "ERROR: 预先放置于 container/lxserver-artifact/ 后再打包。" >&2; \
            exit 1; \
        fi; \
    else \
        echo "[INFO] 使用预置离线包 ${LXSERVER_ZIP_NAME}（无需联网）"; \
    fi; \
    ACTUAL_SHA="$(sha256sum "${TARGET_ZIP}" | awk '{print $1}')"; \
    if [ -n "${LXSERVER_SHA256:-}" ] && [ "${ACTUAL_SHA}" != "${LXSERVER_SHA256}" ]; then \
        echo "ERROR: lxserver zip 校验和不符 (${ACTUAL_SHA} != ${LXSERVER_SHA256})" >&2; \
        exit 1; \
    fi; \
    mkdir -p /srv/lxserver; \
    unzip -q -o "${TARGET_ZIP}" -d /srv/lxserver-tmp; \
    if [ -d "/srv/lxserver-tmp/lx-music-sync-server" ]; then \
        cp -a /srv/lxserver-tmp/lx-music-sync-server/. /srv/lxserver/; \
    else \
        cp -a /srv/lxserver-tmp/. /srv/lxserver/; \
    fi; \
    if [ -f /srv/lxserver/server/server.js ]; then \
        sed -i 's/const result = await musicSdk\[source\]\.getLyric(songInfo);/const _res = musicSdk[source].getLyric(songInfo); const result = await (_res \&\& _res.promise ? _res.promise : _res);/g' /srv/lxserver/server/server.js; \
    fi; \
    rm -rf /srv/lxserver-tmp /srv/lxserver-artifact; \
    chown -R appuser:appuser /srv/lxserver

# --chown+chmod：镜像权限不依赖构建上下文的 umask/属主
COPY --chown=appuser:appuser musicdl-service/app.py musicdl-service/hardening.py /srv/musicdl-service/
COPY --chown=appuser:appuser musicbox-service/app.py musicbox-service/runner.py musicbox-service/netease_ext.py /srv/musicbox-service/
COPY --chown=appuser:appuser lxmusic-service/app.py lxmusic-service/lxserver_client.py lxmusic-service/verify_source.py /srv/lxmusic-service/
COPY --chown=appuser:appuser webui-service/app.py /srv/webui-service/
COPY --chown=appuser:appuser webui-service/static/ /srv/webui-service/static/
# webui 复用 .env 安全合并（sys.path 上加 /srv 后 import proxy.env_merge），必须随镜像分发
COPY --chown=appuser:appuser proxy/__init__.py proxy/env_merge.py /srv/proxy/
RUN find /srv -name "*.py" -exec chmod 0644 {} + \
    && mkdir -p /repo \
    && chmod 0755 /repo /data

COPY container/supervisord.conf /etc/supervisor/supervisord.conf
COPY container/env_flag.sh container/entrypoint.sh container/healthcheck.sh /usr/local/bin/
# COPY 保留上下文文件 mode：umask 077 检出下 supervisord.conf 会以 600 进镜像，
# appuser 读不了配置导致容器起不来；权限全部显式规范化，不依赖构建上下文
RUN chmod 0755 /usr/local/bin/env_flag.sh /usr/local/bin/entrypoint.sh /usr/local/bin/healthcheck.sh \
    && chmod 0644 /etc/supervisor/supervisord.conf

USER appuser
EXPOSE 8001 8002 8003 8004

HEALTHCHECK --interval=30s --timeout=10s --start-period=20s --retries=3 \
    CMD ["/usr/local/bin/healthcheck.sh"]

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
