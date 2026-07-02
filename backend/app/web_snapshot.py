from __future__ import annotations

import asyncio
import base64
import json
import math
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from urllib.parse import quote

import httpx
import websockets

from backend.app.settings import Settings


class StatusSnapshotError(RuntimeError):
    pass


class StatusPageSnapshotter:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def capture(self) -> bytes:
        browser_path = find_browser_executable(self.settings.status_snapshot_browser_path)
        if browser_path is None:
            raise StatusSnapshotError("没有找到可用的 Edge/Chrome 浏览器。")
        return await capture_full_page_snapshot(
            browser_path=browser_path,
            url=self.settings.status_snapshot_url,
            viewport_width=self.settings.status_snapshot_viewport_width,
            timeout_seconds=self.settings.status_snapshot_timeout_seconds,
        )


def find_browser_executable(configured_path: str = "") -> str | None:
    if configured_path:
        path = Path(configured_path)
        if path.is_file():
            return str(path)
    candidates = [
        Path("C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe"),
        Path("C:/Program Files/Microsoft/Edge/Application/msedge.exe"),
        Path("C:/Program Files/Google/Chrome/Application/chrome.exe"),
        Path("C:/Program Files (x86)/Google/Chrome/Application/chrome.exe"),
    ]
    for path in candidates:
        if path.is_file():
            return str(path)
    for name in ("msedge", "msedge.exe", "chrome", "chrome.exe"):
        found = shutil.which(name)
        if found:
            return found
    return None


async def capture_full_page_snapshot(
    *,
    browser_path: str,
    url: str,
    viewport_width: int,
    timeout_seconds: float,
) -> bytes:
    with tempfile.TemporaryDirectory(prefix="apimonitor-status-snapshot-") as user_data_dir:
        process = _launch_browser(browser_path, user_data_dir)
        try:
            port = await _wait_for_devtools_port(Path(user_data_dir), process, timeout_seconds)
            ws_url = await _create_target(port)
            async with websockets.connect(ws_url, max_size=64 * 1024 * 1024) as websocket:
                client = _CdpClient(websocket)
                await client.send("Page.enable")
                await client.send("Runtime.enable")
                await client.send(
                    "Emulation.setDeviceMetricsOverride",
                    {
                        "width": viewport_width,
                        "height": 1080,
                        "deviceScaleFactor": 1,
                        "mobile": False,
                    },
                )
                await client.send("Page.navigate", {"url": url})
                await _wait_for_document_ready(client, timeout_seconds)
                await asyncio.sleep(2)
                await _select_one_hour(client)
                await asyncio.sleep(1)
                await client.send("Runtime.evaluate", {"expression": "window.scrollTo(0, 0);"})
                return await _capture_full_page(client, viewport_width)
        finally:
            _stop_browser(process)


def _launch_browser(browser_path: str, user_data_dir: str) -> subprocess.Popen:
    args = [
        browser_path,
        "--headless=new",
        "--disable-gpu",
        "--disable-extensions",
        "--disable-background-networking",
        "--ignore-certificate-errors",
        "--no-first-run",
        "--no-default-browser-check",
        "--remote-debugging-port=0",
        f"--user-data-dir={user_data_dir}",
        "about:blank",
    ]
    creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    return subprocess.Popen(
        args,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=creation_flags,
    )


async def _wait_for_devtools_port(
    user_data_dir: Path,
    process: subprocess.Popen,
    timeout_seconds: float,
) -> int:
    port_file = user_data_dir / "DevToolsActivePort"
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise StatusSnapshotError("浏览器进程启动后立即退出。")
        if port_file.exists():
            text = port_file.read_text(encoding="utf-8", errors="ignore").splitlines()
            if text and text[0].strip().isdigit():
                return int(text[0].strip())
        await asyncio.sleep(0.1)
    raise StatusSnapshotError("等待浏览器调试端口超时。")


async def _create_target(port: int) -> str:
    target_url = f"http://127.0.0.1:{port}/json/new?{quote('about:blank', safe='')}"
    async with httpx.AsyncClient(timeout=10) as client:
        try:
            response = await client.put(target_url)
            if response.status_code == 405:
                response = await client.get(target_url)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise StatusSnapshotError(f"创建浏览器页面失败：{exc}") from exc
    data = response.json()
    ws_url = data.get("webSocketDebuggerUrl")
    if not isinstance(ws_url, str) or not ws_url:
        raise StatusSnapshotError("浏览器没有返回页面调试地址。")
    return ws_url


async def _wait_for_document_ready(client: "_CdpClient", timeout_seconds: float) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        result = await client.send(
            "Runtime.evaluate",
            {
                "expression": "document.readyState",
                "returnByValue": True,
            },
        )
        if result.get("result", {}).get("value") == "complete":
            return
        await asyncio.sleep(0.25)
    raise StatusSnapshotError("等待页面加载完成超时。")


async def _select_one_hour(client: "_CdpClient") -> None:
    script = """
(() => {
  const items = Array.from(document.querySelectorAll('button, a, [role="button"]'));
  const target = items.find((node) => /1\\s*小时/.test((node.innerText || node.textContent || '').trim()));
  if (!target) return false;
  target.click();
  return true;
})()
"""
    await client.send(
        "Runtime.evaluate",
        {
            "expression": script,
            "awaitPromise": True,
            "returnByValue": True,
        },
    )


async def _capture_full_page(client: "_CdpClient", viewport_width: int) -> bytes:
    metrics = await client.send("Page.getLayoutMetrics")
    content_size = metrics.get("contentSize") or {}
    width = max(viewport_width, math.ceil(float(content_size.get("width") or viewport_width)))
    height = max(1080, math.ceil(float(content_size.get("height") or 1080)))
    result = await client.send(
        "Page.captureScreenshot",
        {
            "format": "png",
            "fromSurface": True,
            "captureBeyondViewport": True,
            "clip": {
                "x": 0,
                "y": 0,
                "width": width,
                "height": height,
                "scale": 1,
            },
        },
    )
    data = result.get("data")
    if not isinstance(data, str) or not data:
        raise StatusSnapshotError("浏览器截图结果为空。")
    return base64.b64decode(data)


def _stop_browser(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


class _CdpClient:
    def __init__(self, websocket) -> None:
        self.websocket = websocket
        self._message_id = 0

    async def send(self, method: str, params: dict | None = None) -> dict:
        self._message_id += 1
        message_id = self._message_id
        await self.websocket.send(
            json.dumps(
                {
                    "id": message_id,
                    "method": method,
                    "params": params or {},
                }
            )
        )
        while True:
            raw = await self.websocket.recv()
            data = json.loads(raw)
            if data.get("id") != message_id:
                continue
            if "error" in data:
                raise StatusSnapshotError(f"{method} 失败：{data['error']}")
            result = data.get("result") or {}
            return result if isinstance(result, dict) else {}
