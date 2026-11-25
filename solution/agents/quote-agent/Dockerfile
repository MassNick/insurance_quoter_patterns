# quote-agent image for local and e2e runs (Foundry uses the source-zip deploy in deploy/, not this image).
# Build from the solution root so the shared package and the contracts are in the context:
#   docker build -f agents/quote-agent/Dockerfile -t quote-agent .
#   docker run --rm -p 8088:8088 -e MCP_QUOTING_URL=http://host.docker.internal:8101/mcp \
#     -e MCP_A2UI_URL=http://host.docker.internal:8102/mcp quote-agent
FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONNOUSERSITE=1 \
    PIP_NO_CACHE_DIR=1 \
    PORT=8088 \
    QUOTE_AGENT_MODEL=fake \
    QUOTE_CONTRACTS_DIR=/app/contracts \
    ENABLE_SENSITIVE_DATA=false \
    OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT=false

WORKDIR /app
COPY contracts /app/contracts
COPY packages/quote_common /app/packages/quote_common
COPY agents/quote-agent/pyproject.toml agents/quote-agent/main.py /app/agents/quote-agent/
COPY agents/quote-agent/src /app/agents/quote-agent/src
RUN pip install /app/packages/quote_common /app/agents/quote-agent \
    && useradd --create-home --uid 10001 agent

USER agent
EXPOSE 8088
HEALTHCHECK --interval=10s --timeout=3s --retries=6 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8088/readiness', timeout=2)"
CMD ["python", "/app/agents/quote-agent/main.py"]
