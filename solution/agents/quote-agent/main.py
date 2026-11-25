"""Hosted agent entry point (``python main.py``): serves the invocations protocol on PORT (default 8088)."""

import os

from quote_common import logs

from quote_agent.app import create_app

if __name__ == "__main__":
    level = os.environ.get("LOG_LEVEL", "INFO").upper()
    logs.setup(level)  # JSON on stderr with PII redaction; the host keeps its own OTel export
    create_app(log_level=level).run()
