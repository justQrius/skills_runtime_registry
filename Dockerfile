FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app
COPY python/ ./python/
COPY catalog/ ./catalog/
COPY schema/ ./schema/

# No third-party Python dependencies; stdlib only.
# Docker CLI (static binary) so the server can run skill containers
# when /var/run/docker.sock is mounted in.
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates curl \
 && ARCH=$(dpkg --print-architecture | sed 's/amd64/x86_64/;s/arm64/aarch64/') \
 && curl -fsSL "https://download.docker.com/linux/static/stable/${ARCH}/docker-27.3.1.tgz" -o /tmp/docker.tgz \
 && tar -xzf /tmp/docker.tgz -C /tmp && mv /tmp/docker/docker /usr/local/bin/ \
 && rm -rf /tmp/docker* && apt-get purge -y curl && apt-get autoremove -y \
 && rm -rf /var/lib/apt/lists/* && docker --version

VOLUME ["/data"]
ENV SKILL_REGISTRY_DATA=/data/files

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=4)"

CMD ["python", "python/skill_registry/server.py", "--http", "8000", "--host", "0.0.0.0"]
