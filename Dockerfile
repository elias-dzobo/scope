FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim AS api

WORKDIR /app

ENV PYTHONUNBUFFERED=1
ENV UV_COMPILE_BYTECODE=1
ENV UV_LINK_MODE=copy

# Install third-party dependencies first (cached layer).
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

# Copy the full source tree.
COPY . .

# Install internal packages as editable installs so their src/ trees are importable.
RUN uv pip install --no-deps -e packages/agent-core \
 && uv pip install --no-deps -e packages/research-core \
 && uv pip install --no-deps -e packages/provider-integrations \
 && uv pip install --no-deps -e apps/api

ENV PATH="/app/.venv/bin:$PATH"
ENV PYTHONPATH="/app"

CMD ["python", "api_main.py"]
