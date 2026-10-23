FROM python:3.12-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_DISABLE_PIP_VERSION_CHECK=1 PIP_NO_CACHE_DIR=1
RUN set -eux; apt-get update; apt-get install -y --no-install-recommends ca-certificates curl ffmpeg tini unzip; ffmpeg -version; rm -rf /var/lib/apt/lists/*
RUN set -eux; curl -fsSL https://deno.land/install.sh -o /tmp/deno-install.sh; DENO_INSTALL=/usr/local sh /tmp/deno-install.sh; rm -f /tmp/deno-install.sh; deno --version
WORKDIR /opt/mediamate
ENV PATH="/opt/mediamate/venv/bin:/usr/local/bin:/usr/bin:/bin"
COPY requirements.txt ./requirements.txt
RUN python -m venv /opt/mediamate/venv && /opt/mediamate/venv/bin/pip install --no-cache-dir -r requirements.txt && /opt/mediamate/venv/bin/python -c "import aiogram, fastapi, yt_dlp; print('Python dependencies OK')"
COPY app ./app
COPY assets ./assets
COPY bot.py ./bot.py
COPY README.md ./.project-readme
RUN set -eux; mkdir -p /tmp/media-bot; useradd --create-home --uid 10001 appuser; chown -R appuser:appuser /opt/mediamate /tmp/media-bot
USER appuser
EXPOSE 10000
ENTRYPOINT ["tini", "--"]
CMD ["/opt/mediamate/venv/bin/python", "-m", "app.main"]
