FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:0.10 /uv /bin/uv
WORKDIR /app
COPY pyproject.toml uv.lock .python-version ./
RUN uv sync --frozen --no-dev --no-install-project
COPY src ./src
ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1
CMD ["python", "-m", "src.main"]
