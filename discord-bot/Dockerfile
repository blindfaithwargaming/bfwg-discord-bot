# syntax=docker/dockerfile:1

FROM python:3.12-slim AS runtime

ARG GIT_COMMIT=""
ARG BOT_UID=10001
ARG BOT_GID=10001

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    SUMMARIES_DIR=/app/summaries \
    GIT_COMMIT=$GIT_COMMIT

WORKDIR /app

RUN groupadd --gid "${BOT_GID}" bot \
    && useradd --uid "${BOT_UID}" --gid "${BOT_GID}" \
      --home-dir /app --no-create-home --shell /usr/sbin/nologin bot

COPY requirements.txt .
RUN python -m pip install --upgrade pip && python -m pip install -r requirements.txt

COPY bot ./bot
COPY content ./content
COPY summaries ./summaries

# Writable mount point for scheduler duplicate-protection state (see compose / VM -v mount).
RUN mkdir -p /app/state && chown bot:bot /app/state

USER bot

CMD ["python", "-m", "bot"]
