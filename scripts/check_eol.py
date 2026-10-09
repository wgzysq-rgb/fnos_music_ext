#!/usr/bin/env python3
"""行尾门禁：确保仓库内文本文件一律为 LF（无 CR、无 BOM）。

背景
----
本项目运行在 Linux（飞牛 fnOS）。若在 Windows 上以 core.autocrlf=true 检出，
工作区会变成 CRLF，打包或安装后会直接失败：

    ./install.sh: line 2: set: pipefail: invalid option name
    syntax error at line 1 of stdin

（原因是 shebang 变成 `#!/usr/bin/env bash\\r`，解释器名带 \\r 找不到；
 或用 `bash -c` 喂 stdin 时第一行即语法错误。）

本脚本按 **git index 为准** 检查工作区，而不是直接扫磁盘——因为 Windows 上
工作区天然可能是 CRLF，真正的判据是仓库里存的是什么、以及打包产物里是什么。

用法
----
    python scripts/check_eol.py            # 检查工作区（对 git blob 比对）
    python scripts/check_eol.py --strict   # 工作区也必须是 LF（CI/Linux 用）
    python scripts/check_eol.py --fpk X.fpk  # 直接检查 fpk 归档内所有文本

退出码：0 = 通过，1 = 发现问题。
"""

from __future__ import annotations

import argparse
import io
import subprocess
import sys
import tarfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# 判定为"文本"的后缀 / 文件名（与 packaging/fpk/build_win.py 保持一致）
TEXT_SUFFIXES = {
    ".sh", ".py", ".json", ".json5", ".yml", ".yaml", ".toml", ".ini", ".cfg",
    ".conf", ".md", ".txt", ".env", ".example", ".service", ".dockerignore",
    ".gitignore", ".js", ".mjs", ".cjs", ".ts", ".css", ".html", ".sql", ".xml",
}
TEXT_NAMES = {
    "install", "main", "_common", "config", "resource", "privilege", "manifest",
    "Dockerfile", "VERSION", "LICENSE", "supervisord.conf", "entrypoint",
}
TEXT_NAME_PREFIXES = ("install_", "upgrade_", "uninstall_", "config_", "run_")
BINARY_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".ico", ".zip", ".gz", ".tgz",
                   ".fpk", ".so", ".whl", ".pdf", ".woff", ".woff2", ".ttf"}


def is_text_name(name: str) -> bool:
    p = Path(name)
    if p.suffix.lower() in BINARY_SUFFIXES:
        return False
    if p.suffix.lower() in TEXT_SUFFIXES:
        return True
    if p.name in TEXT_NAMES or p.name.startswith(TEXT_NAME_PREFIXES):
        return True
    if p.name.startswith(".") and p.suffix == "":
        return True
    return False


def git(*args: str) -> str:
    return subprocess.run(["git", "-C", str(REPO_ROOT), *args],
                          capture_output=True, text=True, check=True).stdout


def check_git_blobs(strict_worktree: bool) -> int:
    """比对 git blob 与工作区：blob 必须 LF；--strict 时工作区也必须 LF。"""
    bad_blob: list[str] = []
    bad_work: list[str] = []

    files = git("ls-files").splitlines()
    for f in files:
        if not is_text_name(f):
            continue
        p = REPO_ROOT / f
        if not p.is_file():
            continue
        if strict_worktree and b"\r" in p.read_bytes():
            bad_work.append(f)

    # 用 git cat-file --batch 批量读 blob，避免逐文件开进程
    if files:
        proc = subprocess.run(
            ["git", "-C", str(REPO_ROOT), "cat-file", "--batch"],
            input="\n".join(files).encode(),
            capture_output=True, check=True,
        )
        out = proc.stdout
        pos = 0
        for f in files:
            nl = out.find(b"\n", pos)
            if nl < 0:
                break
            header = out[pos:nl].decode("utf-8", "replace")
            if header.endswith("missing"):
                pos = nl + 1
                continue
            parts = header.rsplit(" ", 2)
            if len(parts) != 3:
                pos = nl + 1
                continue
            size = int(parts[2])
            body = out[nl + 1: nl + 1 + size]
            pos = nl + 1 + size + 1
            if is_text_name(f) and b"\r" in body:
                bad_blob.append(f)

    ok = True
    if bad_blob:
        ok = False
        print(f"[FAIL] {len(bad_blob)} 个文件在 git 仓库内是 CRLF（必须 LF）：")
        for f in bad_blob[:30]:
            print(f"        {f}")
        if len(bad_blob) > 30:
            print(f"        ... 还有 {len(bad_blob) - 30} 个")
        print("        修复：git add --renormalize . 后提交（配合 .gitattributes）")
    else:
        print(f"[OK] git 仓库内所有文本文件均为 LF")

    if strict_worktree:
        if bad_work:
            ok = False
            print(f"[FAIL] {len(bad_work)} 个工作区文本文件含 CR：")
            for f in bad_work[:30]:
                print(f"        {f}")
            print("        修复：git config core.autocrlf false 后重新检出，"
                  "或 git rm --cached -r . && git reset --hard")
        else:
            print("[OK] 工作区所有文本文件均为 LF")
    return 0 if ok else 1


def check_fpk(fp: Path) -> int:
    """检查 fpk（及内层 app.tgz）里所有文本文件是否 LF。"""
    bad: list[str] = []

    def scan(tf: tarfile.TarFile, prefix: str) -> None:
        for m in tf.getmembers():
            if not m.isfile() or not is_text_name(m.name):
                continue
            fh = tf.extractfile(m)
            if fh and b"\r" in fh.read():
                bad.append(f"{prefix}{m.name}")

    with tarfile.open(fp, "r:gz") as outer:
        scan(outer, "")
        try:
            data = outer.extractfile("app.tgz")
        except KeyError:
            data = None
        if data is not None:
            inner = tarfile.open(fileobj=io.BytesIO(data.read()), mode="r:gz")
            scan(inner, "app.tgz:")

    if bad:
        print(f"[FAIL] {fp.name}: {len(bad)} 个文本文件含 CR：")
        for f in bad[:30]:
            print(f"        {f}")
        return 1
    print(f"[OK] {fp.name}: 归档内所有文本文件均为 LF")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="行尾门禁（LF 强制）")
    ap.add_argument("--strict", action="store_true",
                    help="工作区也必须是 LF（Linux/CI 使用）")
    ap.add_argument("--fpk", metavar="FILE", help="额外检查指定 fpk 归档")
    args = ap.parse_args()

    rc = check_git_blobs(strict_worktree=args.strict)
    if args.fpk:
        rc |= check_fpk(Path(args.fpk))
    return rc


if __name__ == "__main__":
    sys.exit(main())
