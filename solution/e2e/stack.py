"""Start the whole solution locally, with the fake model and no Azure.

    python e2e/stack.py up            # standard ports (8000, 8088, 8101, 8102) until Ctrl-C
    python e2e/stack.py up --offset 10000 --web   # 18000, 18088, ... and the web preview on 15173

Each service runs from its own ``.venv`` (create them with each component's README). The same ``Stack`` class
is the pytest fixture for the API end-to-end tests and the Playwright ``webServer`` for the browser tests.
"""

from __future__ import annotations

import argparse
import os
import signal
import socket
import subprocess
import sys
import time
import urllib.request
from contextlib import suppress
from pathlib import Path

# the solution root
ROOT = Path(__file__).resolve().parents[1]
# dev-only keys the BFF needs to run locally
DEV_KEYS = {"SESSION_REF_KEY": "dev-only-local-key", "SESSION_COOKIE_KEY": "dev-only-session-cookie-key"}


class Stack:
    """The local stack: mcp-quoting, mcp-a2ui, the quote-agent (fake model), the BFF and, optionally, the built web app,
    each a process of its own ``.venv``. A context manager: it starts the services, waiting for each to be healthy,
    and stops them on exit."""

    def __init__(self, offset: int = 0, *, web: bool = False, log_dir: Path | None = None) -> None:
        """``offset`` is added to every port, ``web`` adds the web app, ``log_dir`` receives one log per service."""
        self.ports = {name: port + offset for name, port in {"bff": 8000, "agent": 8088, "quoting": 8101, "a2ui": 8102, "web": 5173}.items()}
        self.web = web
        self.log_dir = log_dir or ROOT / "e2e" / ".logs"
        self.procs: list[subprocess.Popen] = []

    def url(self, name: str) -> str:
        """Base URL of the service ``name``."""
        return f"http://127.0.0.1:{self.ports[name]}"

    def services(self) -> list[tuple[str, list[str], Path, dict[str, str], str]]:
        """(name, command, working directory, environment, health path) of each service, in start order."""
        p = self.ports
        common = {"LOG_LEVEL": "INFO", **DEV_KEYS}
        services = [
            ("quoting", [".venv/bin/python", "-m", "mcp_quoting"], ROOT / "mcp/quoting", {"PORT": str(p["quoting"]), "BFF_BASE_URL": self.url("bff")}, "/healthz"),
            ("a2ui", [".venv/bin/python", "-m", "mcp_a2ui"], ROOT / "mcp/a2ui", {"PORT": str(p["a2ui"])}, "/healthz"),
            ("agent", [".venv/bin/python", "main.py"], ROOT / "agents/quote-agent", {
                "PORT": str(p["agent"]), "QUOTE_AGENT_MODEL": "fake",
                "MCP_QUOTING_URL": f"{self.url('quoting')}/mcp", "MCP_A2UI_URL": f"{self.url('a2ui')}/mcp",
            }, "/readiness"),
            ("bff", [".venv/bin/quote-bff"], ROOT / "bff", {
                "PORT": str(p["bff"]), "HOSTED_AGENT_URL": self.url("agent"), "COOKIE_SECURE": "false",
                "QUOTING_MCP_URL": f"{self.url('quoting')}/mcp", "A2UI_MCP_URL": f"{self.url('a2ui')}/mcp",
                "CORS_ORIGINS": f"http://127.0.0.1:{p['web']},http://localhost:{p['web']}",
            }, "/api/health"),
        ]
        if self.web:
            services.append(("web", ["npm", "run", "preview", "--", "--port", str(p["web"]), "--strictPort", "--host", "127.0.0.1"], ROOT / "web", {
                "BFF_PROXY_TARGET": self.url("bff"),  # vite preview proxies /api (same origin, as behind Front Door)
            }, "/"))
        return [(n, cmd, cwd, {**common, **env}, health) for n, cmd, cwd, env, health in services]

    def __enter__(self) -> Stack:
        """Start the services in order and refuse to start on a busy port; stop them all again if one fails."""
        self.log_dir.mkdir(parents=True, exist_ok=True)
        busy = [f"{n}:{port}" for n, port in self.ports.items() if (n != "web" or self.web) and not _port_free(port)]
        if busy:  # a leftover service would answer the health checks with old code
            raise RuntimeError(f"ports already in use: {', '.join(busy)}")
        try:
            for name, cmd, cwd, env, health in self.services():
                log = (self.log_dir / f"{name}.log").open("w")
                self.procs.append(subprocess.Popen(cmd, cwd=cwd, env={**os.environ, **env}, stdout=log, stderr=subprocess.STDOUT, start_new_session=True))
                self._wait(name, self.url(name) + health)
        except BaseException:
            self.__exit__()
            raise
        return self

    def _wait(self, name: str, url: str, timeout: float = 60) -> None:
        """Poll ``url`` until the service answers (status under 500); fail if it exits early or
        ``timeout`` seconds pass."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.procs[-1].poll() is not None:
                raise RuntimeError(f"{name} exited early; see {self.log_dir / (name + '.log')}")
            with suppress(OSError):
                with urllib.request.urlopen(url, timeout=2) as r:  # noqa: S310 - local health check
                    if r.status < 500:
                        return
            time.sleep(0.3)
        raise TimeoutError(f"{name} not healthy at {url}; see {self.log_dir / (name + '.log')}")

    def __exit__(self, *exc: object) -> None:
        """Stop every service by signalling its own process group (never by name), the last started first."""
        for proc in reversed(self.procs):
            with suppress(ProcessLookupError):
                os.killpg(proc.pid, signal.SIGTERM)  # our own process group only, never by name
        for proc in self.procs:
            with suppress(subprocess.TimeoutExpired):
                proc.wait(timeout=10)
        self.procs.clear()


def _port_free(port: int) -> bool:
    """True when nothing listens on ``port`` of localhost."""
    with socket.socket() as sock:
        return sock.connect_ex(("127.0.0.1", port)) != 0


def main() -> None:
    """``stack.py up``: start the stack and run until interrupted."""
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=["up"])
    parser.add_argument("--offset", type=int, default=0, help="add to every port (10000 → 18000, 18088, ...)")
    parser.add_argument("--web", action="store_true", help="also serve the built web app (run `npm run build` in web/ first)")
    args = parser.parse_args()
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))  # `kill` stops the services too, not only this script
    with Stack(args.offset, web=args.web) as stack:
        print("up:", ", ".join(f"{n} {stack.url(n)}" for n in stack.ports if n != "web" or args.web), flush=True)
        with suppress(KeyboardInterrupt):
            signal.pause()


if __name__ == "__main__":
    sys.exit(main())
