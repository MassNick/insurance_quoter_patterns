# mcp-quoting. The build context is the solution folder, because the image also needs quote_common and the contracts:
#   docker build -f mcp/quoting/Dockerfile -t mcp-quoting:dev solution
#   docker run --rm -p 8101:8101 -e SESSION_REF_KEY=dev-only-local-key -e BFF_BASE_URL=http://host.docker.internal:8000 mcp-quoting:dev
FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app
COPY packages/quote_common /tmp/build/quote_common
COPY mcp/quoting /tmp/build/quoting
# `azure` extra: Cosmos state reader, Entra tokens, Application Insights export.
RUN pip install /tmp/build/quote_common "/tmp/build/quoting[azure]" && rm -rf /tmp/build

# The contracts the server serves (tool names, descriptions, schemas) and the state catalog.
COPY contracts /app/contracts
ENV QUOTE_CONTRACTS_DIR=/app/contracts \
    HOST=0.0.0.0 \
    PORT=8101

RUN useradd --system --uid 10001 --no-create-home app
USER 10001

EXPOSE 8101
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s \
    CMD ["python", "-c", "import os, urllib.request; urllib.request.urlopen(f'http://127.0.0.1:{os.environ[\"PORT\"]}/healthz', timeout=2)"]
CMD ["python", "-m", "mcp_quoting"]
