# mcp-a2ui. The build context is the solution folder, because the image also needs quote_common and the contracts:
#   docker build -f mcp/a2ui/Dockerfile -t mcp-a2ui:dev solution
#   docker run --rm -p 8102:8102 mcp-a2ui:dev
FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# quote_common first: mcp-a2ui depends on it, and it is not on PyPI. The `azure` extra is the Entra check and the Application Insights export.
COPY packages/quote_common/pyproject.toml /build/quote_common/pyproject.toml
COPY packages/quote_common/src /build/quote_common/src
COPY mcp/a2ui/pyproject.toml /build/a2ui/pyproject.toml
COPY mcp/a2ui/src /build/a2ui/src
RUN pip install /build/quote_common "/build/a2ui[azure]" && rm -rf /build

# The contracts are baked in (state catalog, tool descriptions, FormSpec schema): this app has no data-plane access.
COPY contracts /app/contracts
ENV QUOTE_CONTRACTS_DIR=/app/contracts \
    HOST=0.0.0.0 \
    PORT=8102

RUN useradd --system --uid 10001 --no-create-home app
USER 10001

EXPOSE 8102
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s \
    CMD ["python", "-c", "import os, urllib.request; urllib.request.urlopen(f'http://127.0.0.1:{os.environ[\"PORT\"]}/healthz', timeout=2)"]
CMD ["python", "-m", "mcp_a2ui"]
