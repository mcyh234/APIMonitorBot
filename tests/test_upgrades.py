from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

import pytest

from backend.app.upgrades import (
    UpgradeError,
    create_upgrade_package,
    install_upgrade_package,
    load_upgrade_status,
    validate_upgrade_package,
)


def write_file(root: Path, relative: str, content: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def make_source(root: Path, version: str, app_content: str) -> None:
    write_file(root, "backend/app/example.py", app_content)
    write_file(root, "backend/app/main.py", "APP = True\n")
    write_file(root, "frontend/dist/index.html", f"<title>{version}</title>")
    write_file(root, "scripts/helper.py", "print('helper')\n")
    write_file(root, "scripts/restart_after_upgrade.py", "print('restart')\n")
    write_file(root, "requirements.txt", "fastapi>=0.135.0\n")
    write_file(root, "run.py", "print('run')\n")


def test_create_upgrade_package_excludes_runtime_and_secret_files(tmp_path: Path):
    make_source(tmp_path, "1.2.3", "VALUE = 'new'\n")
    write_file(tmp_path, ".env", "SECRET_MASTER_KEY=secret\n")
    write_file(tmp_path, "data/apimonitor.sqlite3", "database")
    write_file(tmp_path, "frontend/node_modules/pkg/index.js", "module")
    write_file(tmp_path, "release/old.zip", "archive")

    package = create_upgrade_package(tmp_path, "1.2.3")
    validated = validate_upgrade_package(package)

    assert validated.info.version == "1.2.3"
    assert "backend/app/example.py" in validated.files
    assert "frontend/dist/index.html" in validated.files
    assert validated.files["VERSION"] == b"1.2.3\n"
    assert ".env" not in validated.files
    assert not any(path.startswith("data/") for path in validated.files)
    assert not any("node_modules" in path for path in validated.files)
    assert not any(path.startswith("release/") for path in validated.files)


def test_install_upgrade_updates_files_preserves_env_and_creates_backup(tmp_path: Path):
    installed = tmp_path / "installed"
    source = tmp_path / "source"
    make_source(installed, "1.0.0", "VALUE = 'old'\n")
    write_file(installed, "VERSION", "1.0.0\n")
    write_file(installed, ".env", "SECRET_MASTER_KEY=keep-me\n")
    make_source(source, "1.1.0", "VALUE = 'new'\n")
    package = create_upgrade_package(source, "1.1.0")

    result = install_upgrade_package(package, installed, install_dependencies=False)

    assert result.version == "1.1.0"
    assert result.previous_version == "1.0.0"
    assert (installed / "backend/app/example.py").read_text(encoding="utf-8") == "VALUE = 'new'\n"
    assert (installed / ".env").read_text(encoding="utf-8") == "SECRET_MASTER_KEY=keep-me\n"
    assert (installed / "VERSION").read_text(encoding="utf-8") == "1.1.0\n"
    backup = installed / result.backup_path
    assert (backup / "files/backend/app/example.py").read_text(encoding="utf-8") == "VALUE = 'old'\n"
    status = load_upgrade_status(installed)
    assert status["current_version"] == "1.1.0"
    assert status["last_installed_version"] == "1.1.0"
    assert status["last_backup_path"] == result.backup_path


def test_validate_upgrade_package_rejects_modified_file(tmp_path: Path):
    make_source(tmp_path, "1.2.3", "VALUE = 'signed'\n")
    package = create_upgrade_package(tmp_path, "1.2.3")
    source = zipfile.ZipFile(io.BytesIO(package), "r")
    output = io.BytesIO()
    with source, zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as target:
        for item in source.infolist():
            content = source.read(item.filename)
            if item.filename == "backend/app/example.py":
                content = b"VALUE = 'tampered'\n"
            target.writestr(item, content)

    with pytest.raises(UpgradeError, match="校验失败"):
        validate_upgrade_package(output.getvalue())


def test_validate_upgrade_package_rejects_path_traversal():
    content = b"bad"
    manifest = {
        "app": "APIMonitorBot",
        "format_version": 1,
        "version": "1.0.0",
        "created_at": "2026-07-10T00:00:00+00:00",
        "files": [
            {
                "path": "../outside.py",
                "size": len(content),
                "sha256": "2f05d4b689d270cafb02285f35f44866d8b1e1170bde7e383e5d6f100adf3b74",
            }
        ],
    }
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr("upgrade-manifest.json", json.dumps(manifest))
        archive.writestr("../outside.py", content)

    with pytest.raises(UpgradeError, match="不允许的路径"):
        validate_upgrade_package(output.getvalue())
