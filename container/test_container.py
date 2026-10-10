"""容器组装离线测试：supervisord.conf / entrypoint 开关选择 / compose 单服务 / Dockerfile。

不依赖 docker：entrypoint 用假的 supervisord/supervisorctl 走真 shell 逻辑。
"""
import configparser
import os
import re
import subprocess
import textwrap
from pathlib import Path

import pytest
import yaml

CONTAINER_DIR = Path(__file__).resolve().parent
REPO_ROOT = CONTAINER_DIR.parent


@pytest.fixture(scope="module")
def supervisord_conf():
    cp = configparser.ConfigParser()
    cp.read(CONTAINER_DIR / "supervisord.conf")
    return cp


def test_supervisord_programs_all_autostart_false(supervisord_conf):
    programs = sorted(s for s in supervisord_conf.sections() if s.startswith("program:"))
    assert programs == [
        "program:lxmusic", "program:lxserver", "program:musicbox", "program:musicdl", "program:webui",
    ]
    for section in programs:
        assert supervisord_conf.get(section, "autostart") == "false"
        assert supervisord_conf.get(section, "autorestart") == "true"
        # 日志汇到容器 stdout，组内进程一起停，避免 uvicorn 子进程残留
        assert supervisord_conf.get(section, "stdout_logfile") == "/dev/stdout"
        assert supervisord_conf.get(section, "stdout_logfile_maxbytes") == "0"
        assert supervisord_conf.get(section, "redirect_stderr") == "true"
        assert supervisord_conf.get(section, "stopasgroup") == "true"
        assert supervisord_conf.get(section, "killasgroup") == "true"


def test_supervisord_ports_and_directories(supervisord_conf):
    expected = {
        "musicdl": ("8001", "/srv/musicdl-service"),
        "musicbox": ("8002", "/srv/musicbox-service"),
        "lxmusic": ("8003", "/srv/lxmusic-service"),
        "webui": ("8004", "/srv/webui-service"),
    }
    for prog, (port, directory) in expected.items():
        section = f"program:{prog}"
        command = supervisord_conf.get(section, "command")
        assert f"--port {port}" in command
        assert supervisord_conf.get(section, "directory") == directory

    # lxserver 独立 node 进程，通过环境变量注入端口与路径
    lxserver_sec = "program:lxserver"
    assert supervisord_conf.get(lxserver_sec, "directory") == "/srv/lxserver"
    assert "node index.js" in supervisord_conf.get(lxserver_sec, "command")
    assert 'PORT="8005"' in supervisord_conf.get(lxserver_sec, "environment")


def test_supervisord_daemon_section(supervisord_conf):
    assert supervisord_conf.get("supervisord", "nodaemon") == "true"
    assert supervisord_conf.get("unix_http_server", "chmod") == "0700"
    assert supervisord_conf.get("supervisorctl", "serverurl") == "unix:///tmp/supervisor.sock"


# ---------------------------------------------------------------- env_flag.sh

def _run_env_flag(env_file: Path, key: str, env_extra: dict | None = None, default: str = "") -> str:
    """source env_flag.sh 后调用 env_flag key，返回标准化输出。"""
    lib = CONTAINER_DIR / "env_flag.sh"
    script = f'. "{lib}"; env_flag {key} {default}\n'
    env = {"PATH": os.environ["PATH"], "FNMUSIC_ENV_FILE": str(env_file)}
    env.update(env_extra or {})
    out = subprocess.run(
        ["sh", "-c", script], capture_output=True, text=True, env=env, timeout=15,
    )
    assert out.returncode == 0, out.stderr
    return out.stdout.strip()


def test_env_flag_reads_quoted_and_export_lines(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "FNMUSIC_LX_ENABLED='true'\n"
        "export FNMUSIC_WEBUI_ENABLED=\"true\"\n"
        "FNMUSIC_MUSICDL_ENABLED=false\n",
        encoding="utf-8",
    )
    assert _run_env_flag(env_file, "FNMUSIC_LX_ENABLED") == "true"
    assert _run_env_flag(env_file, "FNMUSIC_WEBUI_ENABLED") == "true"
    assert _run_env_flag(env_file, "FNMUSIC_MUSICDL_ENABLED") == "false"


def test_env_flag_env_var_and_default_fallback(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("OTHER_KEY=1\n", encoding="utf-8")
    # .env 无该键 → 进程环境变量
    assert _run_env_flag(env_file, "FNMUSIC_LX_ENABLED", {"FNMUSIC_LX_ENABLED": "yes"}) == "true"
    # 都没有 → 默认值
    assert _run_env_flag(env_file, "FNMUSIC_NETEASE_ENABLED") == "false"
    assert _run_env_flag(env_file, "FNMUSIC_NETEASE_ENABLED", default="true") == "true"
    # 非真值关键词一律 false
    assert _run_env_flag(env_file, "FNMUSIC_X", {"FNMUSIC_X": "随便"}) == "false"


def test_env_flag_last_definition_wins(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("FNMUSIC_LX_ENABLED=false\nFNMUSIC_LX_ENABLED=true\n", encoding="utf-8")
    assert _run_env_flag(env_file, "FNMUSIC_LX_ENABLED") == "true"


def test_export_source_env_from_env_file(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "LX_SOURCE_URL='https://example.com/lx.js'\n"
        "LX_SOURCES=kw,kg\n"
        "MUSICDL_SOURCES=kugou,netease\n",
        encoding="utf-8",
    )
    lib = CONTAINER_DIR / "env_flag.sh"
    script = f'. "{lib}"; export_source_env; printenv LX_SOURCE_URL; printenv LX_SOURCES; printenv MUSICDL_SOURCES\n'
    out = subprocess.run(
        ["sh", "-c", script], capture_output=True, text=True,
        env={"PATH": os.environ["PATH"], "FNMUSIC_ENV_FILE": str(env_file)}, timeout=15,
    )
    assert out.returncode == 0, out.stderr
    lines = out.stdout.strip().splitlines()
    assert lines == ["https://example.com/lx.js", "kw,kg", "kugou,netease"]


# ---------------------------------------------------------------- entrypoint.sh

class FakeSupervisor:
    """假 supervisord/supervisorctl：记录调用，status 恒可用。"""

    def __init__(self, bindir: Path, log: Path):
        ctl = bindir / "supervisorctl"
        ctl.write_text(
            textwrap.dedent(f"""\
            #!/bin/sh
            echo "$@" >> "{log}"
            case "$1" in
              pid) exit 0 ;;
              *) exit 0 ;;
            esac
            """),
            encoding="utf-8",
        )
        ctl.chmod(0o755)
        daemon = bindir / "supervisord"
        daemon.write_text(
            textwrap.dedent(f"""\
            #!/bin/sh
            echo "supervisord $*" >> "{log}"
            sleep 0.3
            exit 0
            """),
            encoding="utf-8",
        )
        daemon.chmod(0o755)


def _run_entrypoint(fake_bin: Path, env_file: Path, log: Path, tmp_path: Path | None = None) -> list[str]:
    """跑真实 entrypoint.sh，返回 supervisorctl 的调用序列（去掉 supervisord 行）。"""
    env = {
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "ENV_FLAG_LIB": str(CONTAINER_DIR / "env_flag.sh"),
        "SUPERVISOR_CONF": "/nonexistent/supervisord.conf",
        "FNMUSIC_ENV_FILE": str(env_file),
    }
    if tmp_path is not None:
        data_dir = tmp_path / "data"
        env["DATA_PATH"] = str(data_dir / "lxserver")
        env["LX_DATA_DIR"] = str(data_dir / "lxmusic")
    proc = subprocess.run(
        ["sh", str(CONTAINER_DIR / "entrypoint.sh")],
        capture_output=True, text=True, timeout=30,
        env=env,
    )
    assert proc.returncode == 0, proc.stderr
    calls = [ln for ln in log.read_text(encoding="utf-8").splitlines() if ln and not ln.startswith("supervisord ")]
    calls = [c.replace("-c /nonexistent/supervisord.conf ", "") for c in calls]
    # 套接字就绪探测的 pid 轮询不算启动动作
    return [c for c in calls if c != "pid"]


@pytest.fixture()
def entrypoint_env(tmp_path):
    log = tmp_path / "ctl.log"
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    FakeSupervisor(fake_bin, log)
    env_file = tmp_path / ".env"

    def run(env_text: str) -> list[str]:
        env_file.write_text(env_text, encoding="utf-8")
        log.write_text("", encoding="utf-8")
        return _run_entrypoint(fake_bin, env_file, log, tmp_path)

    return run


@pytest.mark.parametrize("env_text,expected", [
    # 单源：只启动所选音源
    ("FNMUSIC_MUSICDL_ENABLED=true\n", ["start musicdl"]),
    ("FNMUSIC_NETEASE_ENABLED=true\n", ["start musicbox"]),
    ("FNMUSIC_LX_ENABLED=true\n", ["start lxserver", "start lxmusic"]),
    # WebUI 独立开关
    ("FNMUSIC_LX_ENABLED=true\nFNMUSIC_WEBUI_ENABLED=true\n", ["start lxserver", "start lxmusic", "start webui"]),
    ("FNMUSIC_WEBUI_ENABLED=true\n", ["start webui"]),
    # 旧多源并存：只取第一个命中（musicdl 优先），不重复拉起
    ("FNMUSIC_MUSICDL_ENABLED=true\nFNMUSIC_NETEASE_ENABLED=true\nFNMUSIC_LX_ENABLED=true\n",
     ["start musicdl"]),
    # 全关：什么源都不启动
    ("FNMUSIC_MUSICDL_ENABLED=false\n", []),
])
def test_entrypoint_starts_selected_programs(entrypoint_env, env_text, expected):
    assert entrypoint_env(env_text) == expected


# ---------------------------------------------------------------- compose / Dockerfile

def test_compose_single_service_layout():
    compose = yaml.safe_load((REPO_ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    assert list(compose["services"]) == ["fnmusic-sources"]
    svc = compose["services"]["fnmusic-sources"]
    assert svc["build"]["dockerfile"] == "container/Dockerfile"
    assert svc["image"] == "fnmusic-sources:latest"
    # 音源和管理页都只发布在 127.0.0.1；浏览器走飞牛网关
    assert sorted(svc["ports"]) == sorted([
        "127.0.0.1:8768:8001",
        "127.0.0.1:8770:8002",
        "127.0.0.1:8772:8003",
        "127.0.0.1:8774:8004",
    ])
    raw = (REPO_ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    assert "0.0.0.0:8770" not in raw
    assert "0.0.0.0:8774" not in raw
    assert "./sources-data:/data" in svc["volumes"]
    assert any(v.startswith(".:/repo") for v in svc["volumes"])
    assert svc["restart"] == "unless-stopped"
    assert svc["healthcheck"]["test"] == ["CMD", "/usr/local/bin/healthcheck.sh"]


def test_compose_build_args_mirror_passthrough():
    compose = yaml.safe_load((REPO_ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    build = compose["services"]["fnmusic-sources"]["build"]
    args = build["args"]
    for name in ("BASE_IMAGE", "PIP_INDEX_URL", "APT_MIRROR"):
        assert name in args and "${" in args[name], f"构建参数 {name} 需从 .env 透传"
    assert build.get("network") == "host"


def test_dockerfile_assembly():
    text = (CONTAINER_DIR / "Dockerfile").read_text(encoding="utf-8")
    # 单镜像运行时：python + node(lx 沙箱) + ffmpeg(musicdl 试听) + supervisor
    for pkg in ("nodejs", "ffmpeg", "supervisor"):
        assert pkg in text
    for req in ("musicdl-service/requirements.txt", "musicbox-service/requirements.txt",
                "lxmusic-service/requirements.txt", "webui-service/requirements.txt"):
        assert req in text
    assert "container/lxserver.version" in text
    assert "lxmusic-service/lxserver_client.py" in text
    assert "lxmusic-service/js/bridge.js" not in text
    assert text.count("HEALTHCHECK") >= 1
    assert 'ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]' in text
    assert "EXPOSE 8001 8002 8003 8004" in text
    # 非特权运行
    assert "USER appuser" in text
    # 数据卷与仓库挂载点；目录不可 world-writable（bind 挂载后跟宿主机权限）
    assert "/data" in text and "/repo" in text
    assert "chmod 0777" not in text
    # COPY 保留上下文 mode：umask 077 检出下 supervisord.conf 会以 600 进镜像，
    # appuser 读不了配置导致容器起不来；必须显式规范化权限
    assert "chmod 0644 /etc/supervisor/supervisord.conf" in text


def _copied_proxy_modules(text: str) -> set[str]:
    """从 Dockerfile 里收集 COPY 进 /srv/proxy/ 的 proxy 模块名。

    形如 `COPY --chown=... proxy/__init__.py proxy/env_merge.py /srv/proxy/`
    → {"__init__", "env_merge"}。只认目标为 /srv/proxy/ 的那条（可能跨行续行）。
    """
    out: set[str] = set()
    # 先把续行拼成单行，避免 COPY 写成多行时漏解析
    joined = re.sub(r"\\\s*\n\s*", " ", text)
    for line in joined.splitlines():
        line = line.strip()
        if not line.upper().startswith("COPY"):
            continue
        if "/srv/proxy/" not in line:
            continue
        for tok in line.split()[1:]:
            m = re.fullmatch(r"proxy/(.+)\.py", tok)
            if m:
                out.add(m.group(1))
    return out


def test_webui_proxy_imports_ship_in_image():
    """回归（v2.8.0 事故）：webui-service 里 `from proxy import X` 的每个模块
    都必须在 Dockerfile 中 COPY 到 /srv/proxy/。

    背景：webui 在容器内跑，`/repo` 只是挂载进来的仓库、**不在 sys.path 上**；
    它能 import 的 `proxy` 包就是镜像里烤进去的 /srv/proxy/。
    v2.8.0 时该目录只有 __init__.py + env_merge.py，而新代码里写了
    `from proxy import platform_playlists` → ModuleNotFoundError 被兜底 except
    吞掉 → 设置页「分类加载失败」。本机跑测试时仓库根目录能 import 到完整的
    proxy 包，所以单测全绿却线上必炸 —— 这个断言就是为了堵住这类"测试绿、线上炸"。
    """
    text = (CONTAINER_DIR / "Dockerfile").read_text(encoding="utf-8")
    shipped = _copied_proxy_modules(text)
    assert shipped, "Dockerfile 里没有解析到任何 COPY ... /srv/proxy/，解析逻辑或文件已变"

    src = (REPO_ROOT / "webui-service" / "app.py").read_text(encoding="utf-8")
    # 两种写法都要覆盖：`from proxy import x` 与 `from proxy.x import y`
    needed = set(re.findall(r"^\s*from\s+proxy\s+import\s+(\w+)", src, re.M))
    needed |= set(re.findall(r"^\s*from\s+proxy\.(\w+)\s+import", src, re.M))
    assert needed, "没解析到 webui 对 proxy 的导入，正则或源码结构已变"

    missing = sorted(m for m in needed if m not in shipped)
    assert not missing, (
        f"webui-service/app.py 导入了 proxy.{{{','.join(missing)}}}，"
        f"但 Dockerfile 未 COPY 进 /srv/proxy/（已分发：{sorted(shipped)}）。"
        "容器内 /repo 不在 sys.path 上，缺一个就 import 失败。"
    )
    # 每个已分发的 proxy 模块必须在仓库里真实存在
    for name in sorted(shipped):
        assert (REPO_ROOT / "proxy" / f"{name}.py").is_file(), f"proxy/{name}.py 不存在"


def test_proxy_modules_allowed_in_build_context():
    """回归（v2.8.1 真实事故）：Dockerfile 要 COPY 的每个 proxy 模块，
    必须在 .dockerignore 的白名单里被显式放行。

    背景：.dockerignore 第一行是 `*`（排除一切），然后逐个 `!` 放行。
    v2.8.1 我只改了 Dockerfile 的 COPY 清单，忘了同步放行
    `proxy/platform_playlists.py` → 构建期报
    `failed to calculate checksum ...: "/proxy/platform_playlists.py": not found`
    （文件根本不在构建上下文里，Docker 连 checksum 都算不出来）。
    注意镜像构建用了 `build.network: host` / 本地镜像，所以这个错误跟网络无关，
    极易被日志末尾那串"换源/配代理"提示误导。
    """
    text = (CONTAINER_DIR / "Dockerfile").read_text(encoding="utf-8")
    shipped = _copied_proxy_modules(text)
    assert shipped, "Dockerfile 里没解析到 COPY ... /srv/proxy/"

    ig_lines = (REPO_ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()

    # 按顺序求值：`*` 先排除一切，后面的 `!` 逐条放行；目录被排除后无法再包含子文件
    allowed: set[str] = set()
    excluded = True  # 默认被首行 `*` 排除
    for raw in ig_lines:
        s = raw.strip()
        if not s or s.startswith("#"):
            continue
        negate = s.startswith("!")
        pat = s[1:] if negate else s
        if pat == "*":
            excluded = not negate
            continue
        # 目录级：!proxy/ → 目录本身进入上下文，但内部文件仍受 `proxy/*` 约束
        if pat.endswith("/"):
            continue
        # 只关心 proxy/ 下的显式文件名；`proxy/*` 这类通配由 excluded 状态表达
        m = re.fullmatch(r"proxy/(.+)", pat)
        if m and "*" not in m.group(1):
            if negate:
                allowed.add(m.group(1))
            else:
                allowed.discard(m.group(1))
            continue
        if pat.startswith("proxy/*"):
            # `proxy/*` 排除 proxy 下全部条目（与首行 `*` 同效）
            if not negate:
                excluded = True

    missing = sorted(m for m in shipped if f"{m}.py" not in allowed)
    assert not missing, (
        f"Dockerfile 要 COPY proxy/{{{','.join(m + '.py' for m in missing)}}} 进镜像，"
        f"但 .dockerignore 未放行这些文件（已放行：{sorted(allowed)}）。"
        "文件不在构建上下文里 → "
        "`failed to calculate checksum ...: not found`，与网络无关。"
    )
    # 反向：放行了却没人用，属于冗余白名单（保留也无害，但提示清理）
    unused = sorted(a for a in allowed if a not in {f"{m}.py" for m in shipped})
    assert not unused, f".dockerignore 放行了未被 Dockerfile 使用的 proxy 文件：{unused}"


def test_dockerfile_network_fallback_resilience():
    """构建容器网络自愈：始终注入备用公共 DNS；apt 回退看索引落地而非退出码；pip 回退官方源。

    背景：宿主 DNS 指向本机（127.x）时 Docker 构建容器回退 8.8.8.8（国内不可达），
    apt-get update 对 DNS 失败只报 W: 警告且退出码为 0，旧版 `if ! apt-get update`
    的回退永远不触发，最终误报 Unable to locate package。getent 探测本身依赖
    DNS/NSS，缺失或失败还可能被 set -e 打死，因此改为不探测、始终注入。
    """
    text = (CONTAINER_DIR / "Dockerfile").read_text(encoding="utf-8")
    apt_step = text.split("# nodejs（洛雪自定义源")[1].split("# 四个服务的")[0]
    # DNS 自愈：始终注入备用公共 DNS（仅本构建层内生效），不依赖 getent
    assert "getent hosts" not in apt_step
    assert "223.5.5.5" in apt_step and "119.29.29.29" in apt_step
    assert "cat /tmp/resolv.fnmusic" in apt_step
    # 回退触发不能依赖 apt-get update 退出码，必须校验索引真正落地
    assert "if ! apt-get update" not in text
    assert "lists_fetched" in apt_step and "/var/lib/apt/lists" in apt_step
    assert "|| true)" in apt_step  # find 失败不得被 set -e 打死
    # 镜像源与官方源双失败要给出带排查建议的明确错误；禁止 [ERROR] 污染弹窗 grep
    assert "均未取到索引" in apt_step
    assert "daemon.json" in apt_step
    assert 'echo "[ERROR]' not in text
    # pip 步骤：同样自带 DNS 自愈（resolv.conf 改写不跨 RUN 层）+ 官方 PyPI 回退
    pip_step = text.split("# 四个服务的")[1]
    assert "getent hosts" not in pip_step
    assert "223.5.5.5" in pip_step
    assert "https://pypi.org/simple" in pip_step


def test_dockerignore_whitelist_build_context():
    text = (REPO_ROOT / ".dockerignore").read_text(encoding="utf-8")
    code_lines = [ln for ln in text.splitlines() if ln.strip() and not ln.lstrip().startswith("#")]
    # 白名单模式：先忽略一切，再放行四个服务目录与 container/
    assert code_lines[0] == "*"
    for keep in ("!musicdl-service/", "!musicbox-service/", "!lxmusic-service/", "!webui-service/", "!container/"):
        assert keep in code_lines


def test_dockerfile_copies_proxy_modules_imported_by_services():
    """镜像内服务允许 import proxy.*（webui 复用 .env 安全合并）。

    回归：v2.0.0 首次安装时 webui 进程在容器内 ModuleNotFoundError 崩溃——
    Dockerfile 没有 COPY proxy/，.dockerignore 白名单也没放行。
    """
    text = (CONTAINER_DIR / "Dockerfile").read_text(encoding="utf-8")
    copied = " ".join(ln for ln in text.splitlines() if ln.startswith("COPY"))
    ignore = (REPO_ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()
    for service_dir in ("musicdl-service", "musicbox-service", "lxmusic-service", "webui-service"):
        for py in sorted((REPO_ROOT / service_dir).glob("*.py")):
            for m in re.finditer(r"^\s*from proxy\.([A-Za-z_][\w]*)", py.read_text(encoding="utf-8"), re.M):
                mod = f"proxy/{m.group(1)}.py"
                assert mod in copied, f"{service_dir}/{py.name} import {mod}，Dockerfile 需 COPY 进镜像"
                assert f"!{mod}" in ignore, f"{mod} 被 .dockerignore 排除，构建上下文拿不到"
