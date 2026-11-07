FROM python:3.13-slim-bookworm

ARG APP_UID=1000
ARG APP_GID=1000
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    VM_MANAGER_CONFIG=/data/config.toml VM_MANAGER_DATA=/data \
    VM_MANAGER_PUBLIC_KEY=/data/ssh/id_ed25519.pub HOME=/home/app

RUN apt-get update && apt-get install -y --no-install-recommends openssh-client \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid "$APP_GID" app \
    && useradd --uid "$APP_UID" --gid "$APP_GID" --create-home app \
    && mkdir -p /data/ssh /data/uploads \
    && chown -R app:app /data /home/app

WORKDIR /app
COPY requirements.lock pyproject.toml ./
COPY vendor/*.whl /tmp/backend/
RUN pip install --no-cache-dir -r requirements.lock /tmp/backend/*.whl \
    && rm -rf /tmp/backend
COPY vm_manager_docker ./vm_manager_docker
COPY THIRD_PARTY_NOTICES.md ./
RUN pip install --no-cache-dir --no-deps .
COPY docker/ssh.conf /etc/ssh/ssh_config.d/50-vm-manager.conf
COPY docker/entrypoint.sh /usr/local/bin/vm-manager-entrypoint
RUN chmod 755 /usr/local/bin/vm-manager-entrypoint \
    && chmod 644 /etc/ssh/ssh_config.d/50-vm-manager.conf

USER app
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/api/health', timeout=3)"
ENTRYPOINT ["vm-manager-entrypoint"]
CMD ["uvicorn", "vm_manager_docker.app:app", "--host", "0.0.0.0", "--port", "8080", "--workers", "1"]
