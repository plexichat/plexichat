FROM python:3.14.8-slim-trixie

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    git \
    ffmpeg \
    libpq-dev \
    libopus-dev \
    libsrtp2-dev \
    libvpx-dev \
    curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir --require-hashes -r /app/requirements.txt

COPY . /app

# Run the internet-facing FastAPI process as a dedicated non-root user.
#
# Volume ownership notes:
#   - The compose stack mounts the named volume plexichat_home at
#     $HOME/.plexichat (/home/plexichat/.plexichat). Creating that directory
#     owned by UID/GID 10001 in the image means the first-time named-volume
#     population inherits correct ownership, so the app can write
#     data/media/logs/temp there.
#   - It must NOT live under /root: /root is mode 0700 owned by root, so this
#     unprivileged user cannot traverse it, and the server dies on startup with
#     PermissionError while creating /root/.plexichat/logs.
#   - One-shot init steps intentionally stay root: cert-init is a separate
#     container, and deploy tooling invokes config generation with
#     `docker run --user 0` (it writes to host bind mounts).
RUN useradd --uid 10001 --user-group --create-home --shell /usr/sbin/nologin plexichat \
    && mkdir -p /home/plexichat/.plexichat \
    && chown -R plexichat:plexichat /home/plexichat /app

USER plexichat

EXPOSE 8000
EXPOSE 30000-30100/udp

HEALTHCHECK --interval=15s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -sf http://localhost:8000/api/v1/ready

CMD ["python", "main.py"]