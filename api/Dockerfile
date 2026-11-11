FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:0.10 /uv /bin/uv
WORKDIR /app
COPY pyproject.toml uv.lock .python-version ./
RUN uv sync --frozen --no-dev --no-install-project
COPY app ./app
ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1 SQL_DIR=/sql/analysis
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2"]
