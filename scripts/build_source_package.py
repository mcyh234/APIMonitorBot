from __future__ import annotations

import argparse
import hashlib
import json
import zipfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath


PROJECT_ROOT = Path(__file__).resolve().parents[1]
ROOT_FILES = {
    ".editorconfig",
    ".env.example",
    ".gitignore",
    "AGENTS.md",
    "LICENSE",
    "Prompt.txt",
    "README.md",
    "TECHNICAL_MIGRATION_HANDOFF.md",
    "VERSION",
    "pyproject.toml",
    "requirements-dev.txt",
    "requirements.txt",
    "run.py",
}
ROOT_DIRECTORIES = {"backend", "frontend", "scripts", "tests"}
EXCLUDED_PARTS = {
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    ".vite",
    "__pycache__",
    "data",
    "dist",
    "node_modules",
    "release",
}
EXCLUDED_SUFFIXES = {".db", ".log", ".pyc", ".sqlite", ".sqlite3", ".zip"}
REQUIRED_PATHS = {
    "AGENTS.md",
    "README.md",
    "TECHNICAL_MIGRATION_HANDOFF.md",
    "backend/app/main.py",
    "backend/app/monitor.py",
    "frontend/src/main.tsx",
    "tests/test_monitor.py",
}
MANIFEST_NAME = "source-package-manifest.json"


def read_version(root: Path) -> str:
    version = (root / "VERSION").read_text(encoding="utf-8").strip()
    if not version or any(char not in "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz._-" for char in version):
        raise ValueError("VERSION 包含不支持的字符。")
    return version


def is_source_path(relative: str) -> bool:
    path = PurePosixPath(relative)
    if path.is_absolute() or ".." in path.parts or not path.parts:
        return False
    if any(part in EXCLUDED_PARTS for part in path.parts):
        return False
    if path.suffix.lower() in EXCLUDED_SUFFIXES:
        return False
    if len(path.parts) == 1:
        return relative in ROOT_FILES
    return path.parts[0] in ROOT_DIRECTORIES


def collect_source_files(root: Path) -> dict[str, bytes]:
    files: dict[str, bytes] = {}
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.is_symlink():
            continue
        relative = path.relative_to(root).as_posix()
        if is_source_path(relative):
            files[relative] = path.read_bytes()

    missing = sorted(REQUIRED_PATHS - files.keys())
    if missing:
        raise ValueError(f"源码包缺少必要文件：{', '.join(missing)}")
    return files


def build_source_package(root: Path, output: Path) -> tuple[int, int, str]:
    root = root.resolve()
    version = read_version(root)
    files = collect_source_files(root)
    package_root = f"APIMonitorBot-{version}"
    manifest = {
        "app": "APIMonitorBot",
        "package_type": "migration-source",
        "version": version,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "file_count": len(files),
        "files": [
            {
                "path": relative,
                "size": len(content),
                "sha256": hashlib.sha256(content).hexdigest(),
            }
            for relative, content in sorted(files.items())
        ],
    }

    output = output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for relative, content in sorted(files.items()):
            archive.writestr(f"{package_root}/{relative}", content)
        archive.writestr(
            f"{package_root}/{MANIFEST_NAME}",
            json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8"),
        )

    digest = hashlib.sha256(output.read_bytes()).hexdigest()
    return len(files) + 1, output.stat().st_size, digest


def main() -> int:
    parser = argparse.ArgumentParser(description="生成包含完整源码和技术交接文档的迁移包。")
    parser.add_argument("--output", type=Path, help="输出 ZIP 路径。")
    args = parser.parse_args()

    version = read_version(PROJECT_ROOT)
    output = args.output or PROJECT_ROOT / "release" / f"APIMonitorBot-migration-source-{version}.zip"
    file_count, size, digest = build_source_package(PROJECT_ROOT, output)
    print(f"迁移源码包已生成：{output.resolve()}")
    print(f"文件数：{file_count}，大小：{size} bytes")
    print(f"SHA-256：{digest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
