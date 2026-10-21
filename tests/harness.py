"""Shared test harness: serve a fixture app on a real local port."""

from __future__ import annotations

import contextlib
import socket
import threading
import time
from collections.abc import Iterator

import uvicorn


@contextlib.contextmanager
def serve(app: object) -> Iterator[str]:
    """Run `app` under uvicorn on a free local port; yield its base URL."""
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.time() + 10
    while not server.started and time.time() < deadline:
        time.sleep(0.05)
    if not server.started:
        raise RuntimeError("fixture server did not start")
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=10)
