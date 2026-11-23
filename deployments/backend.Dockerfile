FROM ghcr.io/astral-sh/uv:0.10.10 AS uv
FROM python:3.12-slim-bookworm

COPY --from=uv /uv /usr/local/bin/uv
WORKDIR /app
ENV UV_PYTHON_DOWNLOADS=never \
    UV_LINK_MODE=copy \
    PATH="/app/.venv/bin:${PATH}" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --no-install-project
COPY src/ src/
COPY migrations/ migrations/
COPY alembic.ini .
RUN uv sync --locked --no-dev --no-editable \
    && useradd --system --uid 10001 relay \
    && install -d -m 0750 -o relay -g relay /var/lib/relay/attachments
USER relay
EXPOSE 8000
CMD ["uvicorn", "relay.api.app:app", "--host", "0.0.0.0", "--port", "8000"]
