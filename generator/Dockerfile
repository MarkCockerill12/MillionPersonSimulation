FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:0.10 /uv /bin/uv
# pyosmium links against expat (XML) and bzip2 at runtime
RUN apt-get update && apt-get install -y --no-install-recommends libexpat1 libbz2-1.0 && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY pyproject.toml uv.lock .python-version ./
RUN uv sync --frozen --no-dev --no-install-project
COPY src ./src
ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1
CMD ["python", "-m", "src.main"]
