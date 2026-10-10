#!/usr/bin/env python3
"""fnmusic-ext fpk 打包脚本（Windows 原生实现，无需 WSL/Docker/rsync/fnpack）。

与 build.sh 产出等价：dist/fnmusic-ext-<版本>.fpk + .sha256。
.fpk 本质是 tar.gz，内层 app.tgz 已压缩，所以外层只用 tar 归档不再二次压缩
（与 fnpack 一致：app.tgz exist, no need compress it, go ahead）。

用法:
    python packaging/fpk/build_win.py                 # 完整打包
    python packaging/fpk/build_win.py --stage-only DIR
    python packaging/fpk/build_win.py --out dist

设计约束（对齐 build.sh 的 rsync 排除表与 fnpack 的产物契约）：
- 内层 app.tgz 内条目为 ./ 前缀（GNU tar -C app . 的形态）
- 外层 .fpk 条目无 ./ 前缀（fnpack 显式剥离，CI 断言 ^cmd/main$），且目录在后
- manifest.checksum = app.tgz 的 md5
- cmd/* 保留 0755，其余文件 0644，目录 0755
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import tarfile
import time
from pathlib import Path

FPK_DIR = Path(__file__).resolve().parent
REPO_ROOT = FPK_DIR.parent.parent

# rsync 排除表（build.sh 的 --exclude 是"任意层级"语义：'tests/' 会匹配
# proxy/tests/，'.git*' 会匹配 container/lxserver-artifact/.gitignore）
EXCLUDE_DIRS = {
    ".git", ".git.nested", ".github", ".zcode", ".cursor",
    ".tasks_archive", ".tools", ".venv", ".venv-proxy", ".venv-research",
    ".venv-musicdl", ".venv-musicbox", "packaging", "dist", "tests", "docs",
    "__pycache__", ".pytest_cache", "cache", "sources-data", "backup",
    "musicbox-data", "musicdl_outputs", "recommend_cache", "play_history",
    "online_favorites",
}
EXCLUDE_SUFFIXES = (".pyc", ".log", ".part")
EXCLUDE_GLOBS = (".env", "pytest.ini", "CONTRIBUTING.md", "online_favorites.json")
KEEP_OVERRIDES = {".env.example"}  # rsync --include 优先于后面的 --exclude
# 打包中间产物 / 临时输出（Windows 侧可能落在仓库根，避免自污染）。
# 只匹配"点开头或下划线开头的顶层临时物"，不能误伤 __init__.py 之类的源码。
EXCLUDE_PREFIXES = (".tmp-stage", ".build", ".stage", ".verify", "_probe", "_tmp", "_out")
EXCLUDE_TEMP_FILE_RE = re.compile(r"^_.*\.(out|log|txt|json)$")

# 版本正则：x.y.z 或 x.y.z<单字母>（自动迭代构建）
VERSION_RE = re.compile(r"^\d+\.\d+\.\d+[a-z]?$")

# ---------------------------------------------------------------------------
# 行尾规范化
#
# Windows 上 git 常带 core.autocrlf=true，checkout 会把文本文件转成 CRLF，
# 而仓库 blob 里存的是 LF。若原样打进 fpk，NAS(Linux) 上执行会炸：
#   ./install.sh: line 2: set: pipefail: invalid option name
# （shebang 变成 `#!/usr/bin/env bash\r`，解释器名带 \r 找不到；或用
#   `bash -c` 喂 stdin 时先报 `syntax error at line 1 of stdin`）
# 因此打包时统一把文本文件规范化为 LF，与 Linux 上运行 build.sh 的产物一致。
# ---------------------------------------------------------------------------
TEXT_SUFFIXES = {
    ".sh", ".py", ".json", ".json5", ".yml", ".yaml", ".toml", ".ini", ".cfg",
    ".conf", ".md", ".txt", ".env", ".example", ".service", ".dockerignore",
    ".gitignore", ".js", ".mjs", ".cjs", ".ts", ".css", ".html", ".sql", ".xml",
}
# 无后缀但确定是文本（脚本/配置）的文件名
TEXT_NAMES = {
    "install", "main", "_common", "config", "resource", "privilege", "manifest",
    "Dockerfile", "VERSION", "LICENSE", "supervisord.conf", "entrypoint",
}
TEXT_NAME_PREFIXES = ("install_", "upgrade_", "uninstall_", "config_", "run_")
# 扩展名白名单外的"已知文本型配置"后缀（无编码歧义，纯 ASCII 键值对）。
# 例：container/lxserver.version —— 漏掉它会让 `. file` 加载出的变量带 \r，
# 进而把 \r 拼进下载 URL，curl 报 "Malformed input to a URL function"。
TEXT_EXTRA_SUFFIXES = {".version", ".spec", ".lock", ".properties", ".list"}


def _looks_like_text(data: bytes) -> bool:
    """内容探测：无 NUL 字节即视为文本（Git 判定二进制同款启发式）。

    白名单永远会漏（`lxserver.version` 就是这么漏掉的），因此对不在白名单里
    的文件一律回退到内容探测，避免"猜错扩展名 → 行尾未规范化"这类事故。
    """
    if b"\x00" in data:
        return False
    try:
        data.decode("utf-8")
    except UnicodeDecodeError:
        return False
    return True


def _is_text_file(rel: Path, data: bytes | None = None) -> bool:
    if rel.suffix.lower() in TEXT_SUFFIXES or rel.suffix.lower() in TEXT_EXTRA_SUFFIXES:
        return True
    if rel.name in TEXT_NAMES or rel.name.startswith(TEXT_NAME_PREFIXES):
        return True
    if rel.name.startswith(".") and rel.suffix == "":
        return True
    # 白名单未命中：用内容探测兜底（避免遗漏无扩展名 / 罕见扩展名的文本文件）
    if data is not None:
        return _looks_like_text(data)
    return False


def _normalize_eol(data: bytes) -> bytes:
    """CRLF / CR → LF（仅文本文件调用）。"""
    return data.replace(b"\r\n", b"\n").replace(b"\r", b"\n")


def log(msg: str) -> None:
    """打印进度。

    日志里有中文与 ✓ 等符号，而 Windows 控制台默认是 cp936 —— 直接 print 会
    抛 UnicodeEncodeError 让打包**在最后一步失败**（实测：行尾门禁通过后崩）。
    这里显式降级：先尝试原样输出，失败则按当前编码替换不可编码字符。
    """
    text = f"[fpk] {msg}"
    try:
        print(text, flush=True)
    except UnicodeEncodeError:
        enc = getattr(sys.stdout, "encoding", None) or "utf-8"
        sys.stdout.buffer.write(text.encode(enc, "replace") + b"\n")
        sys.stdout.buffer.flush()


def _is_excluded(rel: Path) -> bool:
    """rel 为相对仓库根的路径（文件或目录均可）。

    语义对齐 build.sh 的 rsync --exclude：
    - 'tests/' 等目录名匹配任意层级
    - '.git*' 匹配任意层级的 .git/.gitignore/.github
    - '.env.*' 排除但 .env.example 保留（--include 优先）
    """
    parts = rel.parts
    if any(p in EXCLUDE_DIRS for p in parts):
        return True
    if rel.name in KEEP_OVERRIDES:
        return False
    if any(p.startswith(".git") for p in parts):
        return True
    if rel.name in EXCLUDE_GLOBS or rel.name.startswith(".env."):
        return True
    if rel.name.startswith(EXCLUDE_PREFIXES) or EXCLUDE_TEMP_FILE_RE.match(rel.name):
        return True
    if rel.suffix in EXCLUDE_SUFFIXES:
        return True
    if rel.name == ".DS_Store" or rel.name.endswith(".fpk"):
        return True
    return False


def _iter_payload_files(root: Path):
    """产出 (绝对路径, 相对仓库根的 posix 路径)，已应用排除规则。"""
    for dirpath, dirnames, filenames in os.walk(root):
        d = Path(dirpath)
        rel_dir = d.relative_to(root)
        # 原地裁剪 dirnames，避免走进被排除目录
        dirnames[:] = sorted(n for n in dirnames if not _is_excluded(rel_dir / n))
        for name in sorted(filenames):
            abs_p = d / name
            rel = rel_dir / name
            if _is_excluded(rel):
                continue
            if abs_p.is_symlink():
                continue
            yield abs_p, rel.as_posix()


def _tar_info(name: str, mode: int, size: int = 0, is_dir: bool = False) -> tarfile.TarInfo:
    ti = tarfile.TarInfo(name)
    ti.size = 0 if is_dir else size
    ti.mtime = 0                      # 确定性输出
    ti.mode = mode
    ti.uid = 0
    ti.gid = 0
    ti.uname = "root"
    ti.gname = "root"
    if is_dir:
        ti.type = tarfile.DIRTYPE
    return ti


def _git_exec_map(repo_root: Path) -> dict[str, int]:
    """读取 git index 的权限位，得到 {仓库相对路径: 0o755/0o644}。

    Windows 的 stat 丢失可执行位（.sh 一律 0666），而 rsync -a / tar 在 Linux 上
    会保留仓库真实模式。以 git index 为准才能与 CI 产物权限一致。
    """
    try:
        out = subprocess.run(
            ["git", "-C", str(repo_root), "ls-files", "-s"],
            capture_output=True, text=True, check=True, timeout=60,
        ).stdout
    except Exception:
        return {}
    result: dict[str, int] = {}
    for line in out.splitlines():
        parts = line.split(None, 3)
        if len(parts) != 4:
            continue
        mode, _sha, _stage, path = parts
        result[path.replace("\\", "/")] = 0o755 if mode == "100755" else 0o644
    return result


def _source_mode(rel: Path, git_modes: dict[str, int]) -> int:
    """归档内权限位：优先 git index，回退 0644。

    rel 是相对 stage/app 的路径，形如 "repo/install.sh"；git index 的键是仓库
    相对路径，去掉前导 "repo/" 即可对齐。app/ui/* 不在 git 的该前缀下，走回退。
    """
    key = rel.as_posix()
    if key.startswith("repo/"):
        key = key[len("repo/"):]
    return git_modes.get(key, 0o644)


def _read_member(path: Path, rel: Path) -> bytes:
    """读归档成员内容：文本文件统一规范化为 LF（消除 Windows autocrlf 污染）。"""
    data = path.read_bytes()
    if _is_text_file(rel, data):
        data = _normalize_eol(data)
    return data


def build_app_tgz(stage: Path, out_path: Path) -> None:
    """把 stage/app/repo 打成 app.tgz，条目带 ./ 前缀（GNU tar -C app .）。"""
    src = stage / "app"
    git_modes = _git_exec_map(REPO_ROOT)
    normalized = 0
    with tarfile.open(out_path, "w:gz", format=tarfile.GNU_FORMAT, compresslevel=6) as tf:
        tf.addfile(_tar_info("./", 0o755, is_dir=True))
        for path in sorted(src.rglob("*")):
            rel = path.relative_to(src)
            arc = f"./{rel.as_posix()}"
            if path.is_dir():
                tf.addfile(_tar_info(arc + "/", 0o755, is_dir=True))
            else:
                raw = path.read_bytes()
                data = _read_member(path, rel)
                if data != raw:
                    normalized += 1
                info = _tar_info(arc, _source_mode(rel, git_modes), len(data))
                tf.addfile(info, io.BytesIO(data))
    if normalized:
        log(f"已规范化 {normalized} 个文本文件的行尾为 LF（源工作区为 CRLF）")


def build_fpk(stage: Path, out_path: Path) -> None:
    """把 stage 打成 .fpk（tar.gz），条目无 ./ 前缀，目录条目前置。"""
    entries = sorted(stage.rglob("*"))
    dirs = [p for p in entries if p.is_dir()]
    files = [p for p in entries if p.is_file()]
    with tarfile.open(out_path, "w:gz", format=tarfile.GNU_FORMAT, compresslevel=6) as tf:
        for path in dirs:
            rel = path.relative_to(stage).as_posix()
            tf.addfile(_tar_info(rel + "/", 0o755, is_dir=True))
        for path in files:
            rel_path = path.relative_to(stage)
            rel = rel_path.as_posix()
            mode = 0o755 if path.parent.name == "cmd" else 0o644
            # manifest / wizard / app/ui/config / cmd/* 也要 LF，否则 fnpack
            # 解析 JSON/manifest 或应用中心执行 cmd 脚本时会失败
            data = _read_member(path, rel_path)
            tf.addfile(_tar_info(rel, mode, len(data)), io.BytesIO(data))


def png_size(path: Path) -> tuple[int, int]:
    data = path.read_bytes()
    if data[:8] != b"\x89PNG\r\n\x1a\n" or data[12:16] != b"IHDR":
        raise SystemExit(f"ERROR: {path} 不是合法 PNG")
    return struct.unpack(">II", data[16:24])


def validate_stage(stage: Path, version: str) -> None:
    """复刻 fnpack 的硬校验 + CI 的产物断言，早失败。"""
    manifest = (stage / "manifest").read_text(encoding="utf-8")
    kv = {}
    for line in manifest.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise SystemExit(f"ERROR: manifest 行缺少 =: {line}")
        k, _, v = line.partition("=")
        kv[k.strip()] = v.strip()

    for req in ("appname", "version", "display_name", "desc", "platform",
                "source", "maintainer", "desktop_applaunchname"):
        if req not in kv:
            raise SystemExit(f"ERROR: manifest 缺少必填字段 {req}")
    if kv["appname"] != "fnmusic-ext":
        raise SystemExit(f"ERROR: appname 非法: {kv['appname']}")
    if not VERSION_RE.match(version):
        raise SystemExit(f"ERROR: VERSION 必须是 x.y.z（可加单字母后缀），当前 {version!r}")
    base = re.match(r"\d+\.\d+\.\d+", version).group()
    if kv["version"] != base:
        raise SystemExit(f"ERROR: manifest.version={kv['version']} 与 VERSION 基础版本 {base} 不一致")
    if kv["platform"] not in ("x86", "arm", "loongarch", "risc-v", "all"):
        raise SystemExit(f"ERROR: manifest.platform 非法: {kv['platform']}")

    # desktop_applaunchname 必须命中 app/ui/config 入口
    ui_cfg = json.loads((stage / "app" / "ui" / "config").read_text(encoding="utf-8"))
    if kv["desktop_applaunchname"] not in ui_cfg.get(".url", {}):
        raise SystemExit("ERROR: desktop_applaunchname 未在 app/ui/config 中定义")

    # 图标
    for rel, size in (("ICON.PNG", 64), ("ICON_256.PNG", 256),
                      ("app/ui/images/icon_64.png", 64), ("app/ui/images/icon_256.png", 256)):
        p = stage / rel
        if not p.is_file():
            raise SystemExit(f"ERROR: 缺少图标 {rel}")
        if png_size(p) != (size, size):
            raise SystemExit(f"ERROR: {rel} 尺寸应为 {size}x{size}")

    # cmd 生命周期脚本齐全
    for name in ("main", "install_init", "install_callback", "upgrade_init",
                 "upgrade_callback", "uninstall_init", "uninstall_callback",
                 "config_init", "config_callback"):
        if not (stage / "cmd" / name).is_file():
            raise SystemExit(f"ERROR: 缺少 cmd/{name}")

    # wizard 只允许 install，且 initValue 必须字符串、tips 用 helpText
    wiz = stage / "wizard"
    names = {p.name for p in wiz.iterdir() if p.is_file()}
    if names != {"install"}:
        raise SystemExit(f"ERROR: wizard/ 只应包含 install，实际 {names}")
    steps = json.loads((wiz / "install").read_text(encoding="utf-8"))
    for step in steps:
        for it in step.get("items", []):
            if "initValue" in it and not isinstance(it["initValue"], str):
                raise SystemExit("ERROR: wizard initValue 必须是字符串（fnpack 拒绝布尔值）")
            if it.get("type") == "tips":
                if "helpText" not in it or "text" in it:
                    raise SystemExit("ERROR: wizard tips 必须用 helpText 而不是 text")
            if "field" in it and not it["field"].startswith("wizard_"):
                raise SystemExit(f"ERROR: wizard 字段 {it['field']} 需要 wizard_ 前缀")

    # 载荷必需文件 + 不泄漏开发产物
    repo = stage / "app" / "repo"
    for rel in ("install.sh", "extend.sh", "restore.sh", "docker-compose.yml",
                ".env.example", ".dockerignore", "fnmusic-ext.service", "VERSION",
                "proxy/app.py", "container/Dockerfile", "webui-service/app.py"):
        if not (repo / rel).is_file():
            raise SystemExit(f"ERROR: payload 缺少 {rel}")
    for rel in (".env", ".git", ".github", "tests", "docs", "packaging", "dist"):
        if (repo / rel).exists():
            raise SystemExit(f"ERROR: payload 不应包含 {rel}")
    leaked = [p.name for p in repo.glob(".env*")]
    if leaked != [".env.example"]:
        raise SystemExit(f"ERROR: payload 只应保留 .env.example，实际 {leaked}")
    if list(repo.rglob("__pycache__")):
        raise SystemExit("ERROR: payload 含 __pycache__")


def verify_eol_clean(archive: Path, inner_app_tgz: Path | None = None) -> None:
    """门禁：归档内所有文本文件必须是 LF，否则在 NAS 上执行脚本会直接失败。

    历史事故：Windows core.autocrlf=true 使工作区为 CRLF，install.sh 被打包后
    在飞牛上报 `set: pipefail: invalid option name` / `syntax error at line 1`。
    """
    offenders: list[str] = []

    def _scan(tf: tarfile.TarFile, prefix: str) -> None:
        for m in tf.getmembers():
            if not m.isfile():
                continue
            fh = tf.extractfile(m)
            if fh is None:
                continue
            raw = fh.read()
            # 校验时也走内容探测（与打包期同款判定），否则像 lxserver.version
            # 这种"无已知扩展名的文本"会被漏检 —— 正是本门禁曾失守的原因。
            if not _is_text_file(Path(m.name), raw):
                continue
            if b"\r" in raw:
                offenders.append(f"{prefix}{m.name}")

    with tarfile.open(archive, "r:gz") as outer:
        _scan(outer, "")
        if inner_app_tgz is not None and inner_app_tgz.is_file():
            with tarfile.open(inner_app_tgz, "r:gz") as inner:
                _scan(inner, "app.tgz:")

    if offenders:
        shown = "\n  ".join(offenders[:20])
        raise SystemExit(
            f"ERROR: 归档内有 {len(offenders)} 个文本文件含 CR，会导致 Linux 上执行失败:\n  {shown}"
        )


def main() -> int:
    ap = argparse.ArgumentParser(add_help=True, description="fnmusic-ext fpk 打包（Windows 原生）")
    ap.add_argument("--stage-only", metavar="DIR", default=None,
                    help="只组装不打包（结构测试用）")
    ap.add_argument("--out", metavar="DIR", default=None, help="产物输出目录（默认 dist/）")
    args = ap.parse_args()

    version = (REPO_ROOT / "VERSION").read_text(encoding="utf-8").strip().splitlines()[0].strip()
    if not version:
        raise SystemExit("ERROR: 无法读取 VERSION")
    out_dir = Path(args.out) if args.out else (REPO_ROOT / "dist")
    stage = Path(args.stage_only) if args.stage_only else (FPK_DIR / ".build" / "fpk")

    if stage.exists():
        shutil.rmtree(stage)
    (stage / "app" / "repo").mkdir(parents=True)
    (stage / "app" / "ui").mkdir(parents=True)

    # --- 1. 仓库载荷 --------------------------------------------------------
    repo_dst = stage / "app" / "repo"
    count = 0
    for abs_p, rel in _iter_payload_files(REPO_ROOT):
        dst = repo_dst / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(abs_p, dst)
        count += 1
    log(f"仓库载荷: {count} 个文件 -> {repo_dst.relative_to(REPO_ROOT)}")

    # --- 2. lxserver 离线预编译包（可选） ------------------------------------
    lxver = REPO_ROOT / "container" / "lxserver.version"
    cache_zip = None
    if lxver.is_file():
        env = {}
        for line in lxver.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                env[k.strip()] = v.strip()
        zip_name = env.get("LXSERVER_ZIP_NAME", "")
        cand = FPK_DIR / ".build" / "cache" / zip_name
        if zip_name and cand.is_file():
            expect = env.get("LXSERVER_SHA256", "")
            actual = hashlib.sha256(cand.read_bytes()).hexdigest()
            if expect and actual != expect:
                raise SystemExit(f"ERROR: lxserver 缓存包 sha256 不符 ({actual} != {expect})")
            art = stage / "app" / "repo" / "container" / "lxserver-artifact"
            art.mkdir(parents=True, exist_ok=True)
            shutil.copy2(cand, art / zip_name)
            cache_zip = zip_name
            log(f"已内嵌离线预编译包: {zip_name} ({cand.stat().st_size} bytes)")
        else:
            log(f"[WARN] 缓存缺少 {zip_name or 'lxserver zip'}，fpk 不内嵌（装容器时联网下载）")

    # --- 3. 应用骨架 --------------------------------------------------------
    manifest_ver = re.match(r"\d+\.\d+\.\d+", version).group()
    tmpl = (FPK_DIR / "manifest.in").read_text(encoding="utf-8")
    # newline="\n" 关键：Windows 默认会把 \n 翻成 \r\n，manifest 也必须 LF
    (stage / "manifest").write_text(
        tmpl.replace("@VERSION@", manifest_ver), encoding="utf-8", newline="\n"
    )
    for icon in ("ICON.PNG", "ICON_256.PNG"):
        shutil.copy2(FPK_DIR / icon, stage / icon)
    for sub in ("app/ui", "cmd", "config", "wizard"):
        dst = stage / sub
        if dst.exists():
            shutil.rmtree(dst)
        shutil.copytree(FPK_DIR / sub, dst)
    # cmd 生命周期脚本保留可执行位（fnpack 之外，应用中心也依赖 0755）
    for p in (stage / "cmd").iterdir():
        if p.is_file():
            p.chmod(0o755)

    log(f"组装完成: {stage} (version={version})")

    # --- 4. app.tgz + manifest.checksum ------------------------------------
    app_tgz = stage / "app.tgz"
    build_app_tgz(stage, app_tgz)
    md5 = hashlib.md5(app_tgz.read_bytes()).hexdigest()
    mpath = stage / "manifest"
    text = mpath.read_text(encoding="utf-8")
    if re.search(r"^checksum\s*=", text, re.M):
        text = re.sub(r"^checksum\s*=.*$", f"checksum              = {md5}", text, flags=re.M)
    else:
        if not text.endswith("\n"):
            text += "\n"
        text += f"checksum              = {md5}\n"
    mpath.write_text(text, encoding="utf-8", newline="\n")
    log(f"app.tgz: {app_tgz.stat().st_size} bytes, md5={md5}")

    validate_stage(stage, version)

    if args.stage_only:
        log("仅组装（--stage-only），跳过 fnpack 打包")
        return 0

    # --- 5. 产出 .fpk -------------------------------------------------------
    out_dir.mkdir(parents=True, exist_ok=True)
    final = out_dir / f"fnmusic-ext-{version}.fpk"
    if final.exists():
        final.unlink()
    build_fpk(stage, final)
    verify_eol_clean(final, app_tgz)
    sha = hashlib.sha256(final.read_bytes()).hexdigest()
    # sha256sum -c 要求 LF 行尾（Windows 换行会导致 "No such file or directory"）
    (out_dir / f"{final.name}.sha256").write_bytes(
        f"{sha}  {final.name}\n".encode("ascii")
    )
    log("行尾门禁：归档内文本文件全部为 LF ✓")
    log(f"打包完成: {final} ({final.stat().st_size} bytes)")
    log(f"sha256: {sha}")
    if cache_zip:
        log(f"离线内嵌: {cache_zip}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
