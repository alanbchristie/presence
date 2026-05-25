FROM python:3.14.4-slim-bookworm

# Install uv by copying its static binary from Astral's image.
COPY --from=ghcr.io/astral-sh/uv:0.11.8 /uv /uvx /bin/

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_PROJECT_ENVIRONMENT=/usr/local \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-install-project --no-dev

COPY . .
RUN chmod +x entrypoint.sh

# Application version, supplied by the build pipeline: a git tag if the source
# is tagged, otherwise the short commit SHA (see .github/workflows/build.yml and
# docker-compose.yml). Declared after COPY so a changing value doesn't bust the
# dependency-install cache. Surfaced on the About modal via settings.VERSION.
ARG VERSION=dev
ENV VERSION=${VERSION}

EXPOSE 8000

CMD ["./entrypoint.sh"]
