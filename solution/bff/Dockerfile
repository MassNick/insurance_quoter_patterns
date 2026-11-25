# BFF image. Build from the solution folder (the context needs packages/ and contracts/):
#   docker build -f bff/Dockerfile -t quote-bff .
FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    QUOTE_CONTRACTS_DIR=/app/contracts

WORKDIR /app
COPY packages/quote_common /app/packages/quote_common
COPY contracts /app/contracts
COPY bff/pyproject.toml bff/README.md /app/bff/
COPY bff/src /app/bff/src

RUN pip install /app/packages/quote_common "/app/bff[azure,telemetry]" \
    && useradd --uid 10001 --no-create-home --shell /usr/sbin/nologin bff

USER 10001
EXPOSE 8000
# Container Apps probes: GET /api/health (liveness and readiness).
CMD ["quote-bff"]
