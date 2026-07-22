from __future__ import annotations

import json
import zipfile
from pathlib import Path

from scripts.build_source_package import build_source_package, collect_source_files, is_source_path


def test_source_path_excludes_private_and_generated_files() -> None:
    assert is_source_path("backend/app/main.py")
    assert is_source_path("TECHNICAL_MIGRATION_HANDOFF.md")
    assert not is_source_path(".env")
    assert not is_source_path("data/app.db")
    assert not is_source_path("frontend/dist/index.html")
    assert not is_source_path("frontend/node_modules/react/index.js")
    assert not is_source_path("release/old.zip")


def test_build_source_package_contains_handoff_and_manifest(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[1]
    output = tmp_path / "source.zip"
    expected_files = collect_source_files(root)

    file_count, size, digest = build_source_package(root, output)

    assert file_count == len(expected_files) + 1
    assert size == output.stat().st_size
    assert len(digest) == 64
    with zipfile.ZipFile(output) as archive:
        names = archive.namelist()
        handoff_name = next(name for name in names if name.endswith("/TECHNICAL_MIGRATION_HANDOFF.md"))
        manifest_name = next(name for name in names if name.endswith("/source-package-manifest.json"))
        assert archive.read(handoff_name).decode("utf-8").startswith("# APIMonitorBot 技术迁移")
        manifest = json.loads(archive.read(manifest_name).decode("utf-8"))
        assert manifest["package_type"] == "migration-source"
        assert manifest["file_count"] == len(expected_files)
        assert all(not name.endswith("/.env") for name in names)
        assert all("/data/" not in name for name in names)
