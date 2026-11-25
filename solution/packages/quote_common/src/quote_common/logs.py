"""Structured JSON logs with PII redaction, shared by every Python service.

Logs are English and never carry customer values: the formatter masks emails, phone numbers, postal codes,
VINs and Québec licence numbers in the message and in every ``extra`` field (architecture §13, §15). The
system of record for the conversation is the Cosmos ``events`` container, not the logs.
"""

from __future__ import annotations

import json
import logging
import re
import time
from typing import Any

# What the formatter masks: every match is replaced by "[redacted]".
PII_PATTERNS = [
    re.compile(r"[^\s@]+@[^\s@]+\.[^\s@]+"),  # email
    re.compile(r"\b[A-Z]\d{4}[ -]?\d{6}[ -]?\d{2}\b", re.IGNORECASE),  # Québec licence
    re.compile(r"\b[A-HJ-NPR-Z0-9]{17}\b", re.IGNORECASE),  # VIN
    re.compile(r"\b[ABCEGHJ-NPRSTVXY]\d[ABCEGHJ-NPRSTV-Z][ -]?\d[ABCEGHJ-NPRSTV-Z]\d\b", re.IGNORECASE),  # postal code
    re.compile(r"\(?\b[2-9]\d{2}\)?[ .-]?\d{3}[ .-]?\d{4}\b"),  # NANP phone
]
# The attributes every record has (plus uvicorn's ``color_message``): anything else on a record came from ``extra``.
_STD = set(vars(logging.makeLogRecord({}))) | {"message", "asctime", "color_message"}


def redact(value: Any) -> Any:
    """Mask the PII patterns in a string, or in the strings of dicts, lists and tuples; other values pass through."""
    if isinstance(value, str):
        for pattern in PII_PATTERNS:
            value = pattern.sub("[redacted]", value)
        return value
    if isinstance(value, dict):
        return {k: redact(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [redact(v) for v in value]
    return value


class JsonFormatter(logging.Formatter):
    """One JSON object per line: ts, level, logger, msg, the ``extra`` fields and the error, all redacted."""

    converter = time.gmtime  # UTC everywhere, so logs from every service line up

    def format(self, record: logging.LogRecord) -> str:
        """The record as one JSON line; an exception becomes ``error`` (type and message, no traceback)."""
        ts = self.formatTime(record, "%Y-%m-%dT%H:%M:%S") + f".{int(record.msecs):03d}Z"
        out = {"ts": ts, "level": record.levelname, "logger": record.name}
        out["msg"] = redact(record.getMessage())
        out.update({k: redact(v) for k, v in vars(record).items() if k not in _STD})
        if record.exc_info:
            out["error"] = redact(f"{record.exc_info[0].__name__}: {record.exc_info[1]}")
        return json.dumps(out, ensure_ascii=False, default=str)


def setup(level: str = "INFO", *, otel: bool = False) -> None:
    """Route the root logger to JSON on stderr; with ``otel``, also export to Application Insights.

    ``otel`` needs ``azure-monitor-opentelemetry`` (each service's ``azure`` or ``telemetry`` extra), which reads
    ``APPLICATIONINSIGHTS_CONNECTION_STRING``.
    """
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)
    if otel:
        from azure.monitor.opentelemetry import configure_azure_monitor

        configure_azure_monitor()
