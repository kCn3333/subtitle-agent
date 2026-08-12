FROM python:3.12-slim-bookworm AS base
ARG VCS_REF="unknown"
ARG VERSION="dev"
LABEL org.opencontainers.image.source="https://github.com/kCn3333/subtitle-agent" \
      org.opencontainers.image.description="Lightweight Subtitle Agent foundation" \
      org.opencontainers.image.licenses="MIT" \
      org.opencontainers.image.revision="$VCS_REF" \
      org.opencontainers.image.version="$VERSION"
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg && rm -rf /var/lib/apt/lists/* \
    && groupadd --system --gid 10001 subtitle-agent && useradd --system --uid 10001 --gid subtitle-agent --home-dir /app subtitle-agent
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app app
RUN mkdir -p /data && chown subtitle-agent:subtitle-agent /data

FROM base AS test
USER root
COPY requirements-dev.txt pytest.ini ./
COPY tests tests
RUN pip install --no-cache-dir -r requirements-dev.txt
RUN pytest -q

FROM base AS runtime
USER 10001:10001
VOLUME ["/data"]
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 CMD ["python", "-c", "import json,urllib.request; d=json.load(urllib.request.urlopen('http://127.0.0.1:8080/health', timeout=3)); assert d['status']=='ok'"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
