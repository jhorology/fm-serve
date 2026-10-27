"""Reverse proxy in front of `fm serve` with macOS sleep-prevention keepalive."""

from __future__ import annotations

import asyncio
import re
import socket
import subprocess
import threading
import time
from contextlib import asynccontextmanager
from typing import Any

import httpx
import uvicorn
from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse
from rich.console import Console

from fm_serve import __version__
from fm_serve.keepalive import KeepAliveManager
from fm_serve.terminal import TerminalFmProcess, start_terminal_fm

FmProcess = subprocess.Popen[bytes] | TerminalFmProcess

console = Console()

DEFAULT_PUBLIC_PORT = 1976
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")
HOP_BY_HOP = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailers",
    "transfer-encoding",
    "upgrade",
    "host",
    "content-length",
}


def find_free_port(host: str = "127.0.0.1") -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind((host, 0))
        return int(sock.getsockname()[1])


async def wait_for_url(url: str, timeout: float = 30.0) -> None:
    deadline = time.monotonic() + timeout
    async with httpx.AsyncClient(timeout=2.0) as client:
        while True:
            try:
                resp = await client.get(url)
                if resp.status_code < 500:
                    return
            except Exception:
                pass
            if time.monotonic() >= deadline:
                raise TimeoutError(f"backend not ready: {url}")
            await asyncio.sleep(0.2)


def _stop_fm_process(proc: FmProcess) -> None:
    """fm serve 子プロセスを終了 (terminate → wait → kill)。"""
    if proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass
    if isinstance(proc, TerminalFmProcess):
        proc.close_window()


def create_app(
    *,
    backend_base: str,
    keepalive_timeout: float = 3600.0,
    enable_keepalive: bool = True,
    fm_process: FmProcess | None = None,
) -> FastAPI:
    keepalive = KeepAliveManager(
        timeout_sec=keepalive_timeout, enabled=enable_keepalive
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        client = httpx.AsyncClient(
            base_url=backend_base.rstrip("/"),
            timeout=httpx.Timeout(connect=10.0, read=None, write=30.0, pool=30.0),
        )
        app.state.http = client
        keepalive.acquire()
        monitor_task = asyncio.create_task(keepalive.monitor_loop(check_interval=30.0))
        try:
            yield
        finally:
            monitor_task.cancel()
            try:
                await monitor_task
            except asyncio.CancelledError:
                pass
            keepalive.release()
            await client.aclose()
            if fm_process is not None:
                _stop_fm_process(fm_process)

    app = FastAPI(title="fm-serve", version=__version__, lifespan=lifespan)
    app.state.keepalive = keepalive
    app.state.backend_base = backend_base
    app.state.fm_process = fm_process

    @app.middleware("http")
    async def keepalive_middleware(request: Request, call_next):
        # ヘルスチェック以外のリクエストで keepalive (スリープ抑制) を更新
        if request.url.path not in ("/", "/health"):
            keepalive.touch()
        return await call_next(request)

    @app.get("/")
    @app.get("/health")
    async def health():
        upstream: dict[str, Any] = {}
        upstream_ok = False
        try:
            client: httpx.AsyncClient = app.state.http
            resp = await client.get("/health")
            # readiness 判定 (wait_for_url) と同じく 4xx (例: /health 未実装) は稼働とみなす
            upstream_ok = resp.status_code < 500
            try:
                upstream = resp.json()
            except Exception:
                upstream = {"raw": resp.text}
        except Exception as e:
            upstream = {"error": str(e)}

        fm_running = fm_process is None or fm_process.poll() is None
        status = "healthy" if upstream_ok and fm_running else "degraded"
        return {
            "status": status,
            "service": "fm-serve",
            "backend": backend_base,
            "upstream": upstream,
            "fm_running": fm_running,
            "keepalive": keepalive.status(),
        }

    async def _proxy(request: Request, path: str) -> Response:
        client: httpx.AsyncClient = app.state.http
        target = "/" + path.lstrip("/")
        if request.url.query:
            target = f"{target}?{request.url.query}"

        headers = {
            k: v for k, v in request.headers.items() if k.lower() not in HOP_BY_HOP
        }
        body = await request.body()

        req = client.build_request(
            request.method,
            target,
            headers=headers,
            content=body if body else None,
        )
        upstream = await client.send(req, stream=True)

        resp_headers = {
            k: v for k, v in upstream.headers.items() if k.lower() not in HOP_BY_HOP
        }

        async def stream():
            try:
                async for chunk in upstream.aiter_raw():
                    yield chunk
            finally:
                await upstream.aclose()

        return StreamingResponse(
            stream(),
            status_code=upstream.status_code,
            headers=resp_headers,
            media_type=upstream.headers.get("content-type"),
        )

    @app.api_route(
        "/{path:path}",
        methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"],
    )
    async def proxy_all(path: str, request: Request):
        # / and /health are handled above; this catches everything else including
        # /v1/models and /v1/chat/completions (streaming).
        try:
            return await _proxy(request, path)
        except httpx.HTTPError as e:
            return JSONResponse(
                status_code=502,
                content={"error": "upstream_unavailable", "detail": str(e)},
            )

    return app


def start_fm_process(
    *,
    host: str = "127.0.0.1",
    port: int,
    fm_bin: str = "/usr/bin/fm",
) -> subprocess.Popen[bytes]:
    cmd = [fm_bin, "serve", "--host", host, "--port", str(port)]
    console.print(f"[cyan]>> fm serve 起動: {' '.join(cmd)}[/cyan]")
    return subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )


def run_server(
    *,
    host: str = "127.0.0.1",
    port: int = DEFAULT_PUBLIC_PORT,
    backend_host: str = "127.0.0.1",
    backend_port: int | None = None,
    fm_bin: str = "/usr/bin/fm",
    keepalive_timeout: float = 3600.0,
    enable_keepalive: bool = True,
    terminal: bool = False,
) -> None:
    backend_port = backend_port or find_free_port(backend_host)
    if backend_port == port and backend_host == host:
        raise ValueError("backend_port must differ from public port")

    if terminal:
        console.print("[cyan]>> Terminal.app 経由で fm serve を起動 (PCC)[/cyan]")
        fm_proc: FmProcess = start_terminal_fm(
            host=backend_host, port=backend_port, fm_bin=fm_bin
        )
    else:
        fm_proc = start_fm_process(host=backend_host, port=backend_port, fm_bin=fm_bin)
    backend_base = f"http://{backend_host}:{backend_port}"

    # Drain fm stdout in the background so the pipe does not fill up.
    # Must start BEFORE the readiness wait: a large startup log would fill
    # the pipe buffer and deadlock fm before it becomes healthy.
    def _drain() -> None:
        if not fm_proc.stdout:
            return
        for line in iter(fm_proc.stdout.readline, b""):
            try:
                text = _ANSI_RE.sub("", line.decode(errors="replace")).rstrip()
            except Exception:
                continue
            if text:
                console.print(f"[dim]fm[/dim] {text}")

    threading.Thread(target=_drain, name="fm-serve-drain", daemon=True).start()

    async def _ready() -> None:
        await wait_for_url(f"{backend_base}/health", timeout=45.0)

    try:
        asyncio.run(_ready())
    except Exception:
        _stop_fm_process(fm_proc)
        out = b""
        if fm_proc.stdout:
            try:
                out = fm_proc.stdout.read() or b""
            except Exception:
                pass
        raise RuntimeError(
            f"fm serve failed to become healthy on {backend_base}\n{out.decode(errors='replace')}"
        ) from None

    console.print(
        f"[green]✓ fm serve ready[/green]  backend={backend_base}  "
        f"public=http://{host}:{port}"
    )

    app = create_app(
        backend_base=backend_base,
        keepalive_timeout=keepalive_timeout,
        enable_keepalive=enable_keepalive,
        fm_process=fm_proc,
    )
    server = uvicorn.Server(uvicorn.Config(app, host=host, port=port, log_level="info"))
    monitor_stop = threading.Event()

    def _monitor_backend() -> None:
        while not monitor_stop.wait(1):
            if fm_proc.poll() is not None:
                console.print("[red]fm serve が終了しました。プロキシを停止します。[/red]")
                server.should_exit = True  # launchd KeepAlive restarts both processes.
                return

    monitor = threading.Thread(target=_monitor_backend, daemon=True)
    monitor.start()
    try:
        server.run()
    finally:
        monitor_stop.set()
        monitor.join(timeout=2)
        # lifespan が実行されない場合 (例: ポートバインド失敗) の子プロセス掃除
        _stop_fm_process(fm_proc)
