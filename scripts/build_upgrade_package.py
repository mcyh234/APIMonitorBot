from __future__ import annotations

import argparse
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.app.upgrades import build_frontend, create_upgrade_package, project_root, read_current_version


def main() -> int:
    parser = argparse.ArgumentParser(description="生成 APIMonitorBot 升级包。")
    parser.add_argument("--version", help="升级版本号，默认使用 VERSION 文件。")
    parser.add_argument("--output", type=Path, help="输出 ZIP 路径。")
    parser.add_argument("--skip-frontend-build", action="store_true", help="不重新构建 WebUI。")
    args = parser.parse_args()

    root = project_root()
    version = args.version or read_current_version(root)
    if not args.skip_frontend_build:
        build_frontend(root)
    package = create_upgrade_package(root, version)
    output = args.output or root / "release" / f"APIMonitorBot-upgrade-{version}.zip"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(package)
    print(f"升级包已生成：{output}")
    print(f"版本：{version}，大小：{len(package)} bytes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
