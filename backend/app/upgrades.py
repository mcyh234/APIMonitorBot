from __future__ import annotations

import hashlib
import io
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import threading
import uuid
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any


APP_ID = "APIMonitorBot"
PACKAGE_FORMAT_VERSION = 1
MANIFEST_NAME = "upgrade-manifest.json"
MAX_PACKAGE_BYTES = 100 * 1024 * 1024
MAX_UNCOMPRESSED_BYTES = 250 * 1024 * 1024
MAX_PACKAGE_FILES = 5000
VERSION_PATTERN = re.compile(r"^[0-9A-Za-z][0-9A-Za-z._-]{0,63}$")

ALLOWED_ROOT_FILES = {
    ".env.example",
    ".gitignore",
    "AGENTS.md",
    "LICENSE",
    "Prompt.txt",
    "README.md",
    "VERSION",
    "pyproject.toml",
    "requirements-dev.txt",
    "requirements.txt",
    "run.py",
}
ALLOWED_ROOT_DIRECTORIES = {"backend", "frontend", "scripts", "tests"}
REQUIRED_PACKAGE_PATHS = {
    "VERSION",
    "backend/app/main.py",
    "frontend/dist/index.html",
    "requirements.txt",
    "run.py",
    "scripts/restart_after_upgrade.py",
}
EXCLUDED_PARTS = {
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "__pycache__",
    "data",
    "node_modules",
    "release",
}
EXCLUDED_SUFFIXES = {".db", ".log", ".pyc", ".sqlite", ".sqlite3", ".zip"}


class UpgradeError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class UpgradePackageInfo:
    version: str
    created_at: str
    file_count: int
    total_size: int


@dataclass(frozen=True, slots=True)
class ValidatedUpgrade:
    info: UpgradePackageInfo
    files: dict[str, bytes]


@dataclass(frozen=True, slots=True)
class UpgradeInstallResult:
    version: str
    previous_version: str
    installed_at: str
    updated_files: int
    backup_path: str
    dependencies_installed: bool


def project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def read_current_version(root: Path | None = None) -> str:
    version_file = (root or project_root()) / "VERSION"
    if not version_file.is_file():
        return "development"
    value = version_file.read_text(encoding="utf-8").strip()
    return value or "development"


def validate_version(version: str) -> str:
    clean = version.strip()
    if not VERSION_PATTERN.fullmatch(clean):
        raise UpgradeError("版本号只能包含字母、数字、点、下划线和连字符，最长 64 个字符。")
    return clean


def build_frontend(root: Path | None = None, *, timeout_seconds: int = 300) -> None:
    base = (root or project_root()).resolve()
    frontend = base / "frontend"
    npm_name = "npm.cmd" if os.name == "nt" else "npm"
    npm = shutil.which(npm_name) or shutil.which("npm")
    if npm is None:
        raise UpgradeError("没有找到 npm，无法生成最新的 WebUI 构建产物。")
    try:
        result = subprocess.run(
            [npm, "run", "build"],
            cwd=frontend,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise UpgradeError("WebUI 构建超时。") from exc
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "npm run build failed").strip()[-2000:]
        raise UpgradeError(f"WebUI 构建失败：{detail}")


def create_upgrade_package(root: Path, version: str) -> bytes:
    base = root.resolve()
    clean_version = validate_version(version)
    files: dict[str, bytes] = {}
    for path in sorted(base.rglob("*")):
        if not path.is_file() or path.is_symlink():
            continue
        relative = path.relative_to(base).as_posix()
        if not is_allowed_upgrade_path(relative):
            continue
        files[relative] = path.read_bytes()
    files["VERSION"] = (clean_version + "\n").encode("utf-8")

    created_at = _utc_now_iso()
    manifest_files = [
        {
            "path": relative,
            "size": len(content),
            "sha256": hashlib.sha256(content).hexdigest(),
        }
        for relative, content in sorted(files.items())
    ]
    manifest = {
        "app": APP_ID,
        "format_version": PACKAGE_FORMAT_VERSION,
        "version": clean_version,
        "created_at": created_at,
        "files": manifest_files,
    }

    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        archive.writestr(MANIFEST_NAME, json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8"))
        for relative, content in sorted(files.items()):
            archive.writestr(relative, content)
    return output.getvalue()


def validate_upgrade_package(package: bytes) -> ValidatedUpgrade:
    if not package:
        raise UpgradeError("升级包为空。")
    if len(package) > MAX_PACKAGE_BYTES:
        raise UpgradeError(f"升级包不能超过 {MAX_PACKAGE_BYTES // (1024 * 1024)} MB。")
    try:
        archive = zipfile.ZipFile(io.BytesIO(package), "r")
    except (zipfile.BadZipFile, OSError) as exc:
        raise UpgradeError("文件不是有效的升级包。") from exc

    with archive:
        infos = [item for item in archive.infolist() if not item.is_dir()]
        if len(infos) > MAX_PACKAGE_FILES:
            raise UpgradeError("升级包文件数量超过限制。")
        total_uncompressed = sum(item.file_size for item in infos)
        if total_uncompressed > MAX_UNCOMPRESSED_BYTES:
            raise UpgradeError("升级包解压后的体积超过限制。")
        names = [item.filename for item in infos]
        if len(names) != len(set(names)):
            raise UpgradeError("升级包包含重复文件名。")
        if names.count(MANIFEST_NAME) != 1:
            raise UpgradeError("升级包缺少唯一的 upgrade-manifest.json。")
        for item in infos:
            _validate_zip_entry(item)

        try:
            manifest = json.loads(archive.read(MANIFEST_NAME).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError, KeyError) as exc:
            raise UpgradeError("升级包清单无法解析。") from exc
        version, created_at, entries = _validate_manifest(manifest)

        expected_paths = {entry["path"] for entry in entries}
        archive_paths = set(names) - {MANIFEST_NAME}
        if archive_paths != expected_paths:
            raise UpgradeError("升级包文件与清单不一致。")

        files: dict[str, bytes] = {}
        for entry in entries:
            relative = entry["path"]
            content = archive.read(relative)
            if len(content) != entry["size"]:
                raise UpgradeError(f"文件大小校验失败：{relative}")
            digest = hashlib.sha256(content).hexdigest()
            if digest != entry["sha256"]:
                raise UpgradeError(f"文件完整性校验失败：{relative}")
            files[relative] = content

    return ValidatedUpgrade(
        info=UpgradePackageInfo(
            version=version,
            created_at=created_at,
            file_count=len(files),
            total_size=sum(len(content) for content in files.values()),
        ),
        files=files,
    )


def install_upgrade_package(
    package: bytes,
    root: Path,
    *,
    install_dependencies: bool = True,
) -> UpgradeInstallResult:
    base = root.resolve()
    validated = validate_upgrade_package(package)
    previous_version = read_current_version(base)
    installed_at = _utc_now_iso()
    backup_dir = _create_backup_directory(base, previous_version)
    backup_files = backup_dir / "files"
    requirements_before = _read_optional(base / "requirements.txt")
    requirements_after = validated.files.get("requirements.txt")
    requirements_changed = requirements_after is not None and requirements_after != requirements_before

    applied: list[tuple[str, bool]] = []
    try:
        for relative, content in sorted(validated.files.items()):
            target = _target_path(base, relative)
            existed = target.is_file()
            if existed:
                backup_target = backup_files.joinpath(*PurePosixPath(relative).parts)
                backup_target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(target, backup_target)
            _atomic_write(target, content)
            applied.append((relative, existed))

        dependencies_installed = False
        if requirements_changed and install_dependencies:
            _install_dependencies(base)
            dependencies_installed = True
    except Exception as exc:
        _restore_applied_files(base, backup_files, applied)
        if isinstance(exc, UpgradeError):
            raise
        raise UpgradeError(f"安装升级包失败：{exc}") from exc

    backup_manifest = {
        "previous_version": previous_version,
        "installed_version": validated.info.version,
        "created_at": installed_at,
        "files": [
            {"path": relative, "had_original": existed}
            for relative, existed in applied
        ],
    }
    _write_json(backup_dir / "backup-manifest.json", backup_manifest)
    relative_backup = backup_dir.relative_to(base).as_posix()
    result = UpgradeInstallResult(
        version=validated.info.version,
        previous_version=previous_version,
        installed_at=installed_at,
        updated_files=len(applied),
        backup_path=relative_backup,
        dependencies_installed=dependencies_installed,
    )
    _write_json(
        base / "data" / "upgrades" / "last-install.json",
        {
            "version": result.version,
            "previous_version": result.previous_version,
            "installed_at": result.installed_at,
            "updated_files": result.updated_files,
            "backup_path": result.backup_path,
            "dependencies_installed": result.dependencies_installed,
        },
    )
    return result


def load_upgrade_status(root: Path | None = None) -> dict[str, Any]:
    base = (root or project_root()).resolve()
    status: dict[str, Any] = {
        "current_version": read_current_version(base),
        "process_id": os.getpid(),
        "last_installed_version": None,
        "last_installed_at": None,
        "last_backup_path": None,
    }
    metadata_path = base / "data" / "upgrades" / "last-install.json"
    if not metadata_path.is_file():
        return status
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return status
    status["last_installed_version"] = metadata.get("version")
    status["last_installed_at"] = metadata.get("installed_at")
    status["last_backup_path"] = metadata.get("backup_path")
    return status


def schedule_application_restart(root: Path | None = None, *, exit_delay_seconds: float = 1.5) -> None:
    base = (root or project_root()).resolve()
    helper = base / "scripts" / "restart_after_upgrade.py"
    if not helper.is_file():
        raise UpgradeError("缺少升级重启助手 scripts/restart_after_upgrade.py。")
    command = [sys.executable, str(helper), str(os.getpid()), str(base), sys.executable]
    kwargs: dict[str, Any] = {
        "cwd": base,
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "close_fds": True,
    }
    if os.name == "nt":
        kwargs["creationflags"] = (
            subprocess.CREATE_NEW_PROCESS_GROUP
            | subprocess.DETACHED_PROCESS
            | subprocess.CREATE_NO_WINDOW
        )
    else:
        kwargs["start_new_session"] = True
    try:
        subprocess.Popen(command, **kwargs)
    except OSError as exc:
        raise UpgradeError(f"无法启动升级重启助手：{exc}") from exc

    timer = threading.Timer(exit_delay_seconds, lambda: os._exit(0))
    timer.daemon = True
    timer.start()


def is_allowed_upgrade_path(relative: str) -> bool:
    if not relative or "\\" in relative:
        return False
    path = PurePosixPath(relative)
    parts = path.parts
    if not parts or path.is_absolute() or any(part in {"", ".", ".."} for part in parts):
        return False
    if len(parts) == 1:
        return parts[0] in ALLOWED_ROOT_FILES
    if parts[0] not in ALLOWED_ROOT_DIRECTORIES:
        return False
    if any(part in EXCLUDED_PARTS for part in parts):
        return False
    if any(part == ".env" or part.startswith(".env.") for part in parts):
        return False
    if Path(parts[-1]).suffix.lower() in EXCLUDED_SUFFIXES:
        return False
    return True


def _validate_manifest(manifest: Any) -> tuple[str, str, list[dict[str, Any]]]:
    if not isinstance(manifest, dict):
        raise UpgradeError("升级包清单格式错误。")
    if manifest.get("app") != APP_ID:
        raise UpgradeError("升级包不属于 APIMonitorBot。")
    if manifest.get("format_version") != PACKAGE_FORMAT_VERSION:
        raise UpgradeError("升级包格式版本不受支持。")
    version = validate_version(str(manifest.get("version") or ""))
    created_at = str(manifest.get("created_at") or "")
    entries = manifest.get("files")
    if not created_at or not isinstance(entries, list) or not entries:
        raise UpgradeError("升级包清单缺少必要字段。")
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict):
            raise UpgradeError("升级包文件清单格式错误。")
        relative = str(entry.get("path") or "")
        size = entry.get("size")
        digest = str(entry.get("sha256") or "").lower()
        if not is_allowed_upgrade_path(relative):
            raise UpgradeError(f"升级包包含不允许的路径：{relative or '<empty>'}")
        if relative in seen:
            raise UpgradeError(f"升级包包含重复文件：{relative}")
        if not isinstance(size, int) or size < 0:
            raise UpgradeError(f"升级包文件大小无效：{relative}")
        if not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise UpgradeError(f"升级包文件摘要无效：{relative}")
        normalized.append({"path": relative, "size": size, "sha256": digest})
        seen.add(relative)
    if "VERSION" not in seen:
        raise UpgradeError("升级包缺少 VERSION 文件。")
    missing = sorted(REQUIRED_PACKAGE_PATHS - seen)
    if missing:
        raise UpgradeError(f"升级包不完整，缺少文件：{', '.join(missing)}")
    return version, created_at, normalized


def _validate_zip_entry(item: zipfile.ZipInfo) -> None:
    unix_mode = item.external_attr >> 16
    if unix_mode and stat.S_ISLNK(unix_mode):
        raise UpgradeError(f"升级包不允许符号链接：{item.filename}")
    if item.filename == MANIFEST_NAME:
        return
    if not is_allowed_upgrade_path(item.filename):
        raise UpgradeError(f"升级包包含不允许的路径：{item.filename}")


def _target_path(root: Path, relative: str) -> Path:
    if not is_allowed_upgrade_path(relative):
        raise UpgradeError(f"不允许写入路径：{relative}")
    target = root.joinpath(*PurePosixPath(relative).parts).resolve()
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise UpgradeError(f"升级路径越界：{relative}") from exc
    return target


def _atomic_write(target: Path, content: bytes) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.{uuid.uuid4().hex}.upgrade-tmp")
    try:
        temporary.write_bytes(content)
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


def _create_backup_directory(root: Path, previous_version: str) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    safe_version = re.sub(r"[^0-9A-Za-z._-]+", "-", previous_version)[:64] or "unknown"
    backup = root / "data" / "upgrades" / "backups" / f"{stamp}-{safe_version}-{uuid.uuid4().hex[:8]}"
    backup.mkdir(parents=True, exist_ok=False)
    return backup


def _restore_applied_files(root: Path, backup_files: Path, applied: list[tuple[str, bool]]) -> None:
    for relative, existed in reversed(applied):
        target = _target_path(root, relative)
        backup = backup_files.joinpath(*PurePosixPath(relative).parts)
        try:
            if existed and backup.is_file():
                _atomic_write(target, backup.read_bytes())
            elif not existed:
                target.unlink(missing_ok=True)
        except OSError:
            continue


def _install_dependencies(root: Path) -> None:
    try:
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "pip",
                "install",
                "--disable-pip-version-check",
                "-r",
                str(root / "requirements.txt"),
            ],
            cwd=root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=600,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise UpgradeError("安装新版依赖超时，项目文件已回滚。") from exc
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "pip install failed").strip()[-2000:]
        raise UpgradeError(f"安装新版依赖失败，项目文件已回滚：{detail}")


def _read_optional(path: Path) -> bytes | None:
    try:
        return path.read_bytes()
    except FileNotFoundError:
        return None


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write(path, json.dumps(value, ensure_ascii=False, indent=2).encode("utf-8"))


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
