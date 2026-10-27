FROM python:3.14.8-slim-trixie

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

# ``libbrotli1`` is named explicitly rather than left to curl's dependency:
# CVE-2026-111497 is an out-of-bounds write in the Brotli decoder, fixed for
# trixie in the security update ``1.1.0-2+deb13u1``. Debian's main archive still
# carries ``1.1.0-2+b7``, and apt only upgrades a package that is asked for by
# name (or pulled in by a newer dependency), so an image built from a base that
# already has the old library would keep it. Naming it installs the security
# candidate; ``docker run --rm <image> dpkg -l libbrotli1`` should show
# ``+deb13u1``.
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    git \
    ffmpeg \
    libpq-dev \
    libopus-dev \
    libsrtp2-dev \
    libvpx-dev \
    libbrotli1 \
    curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir --require-hashes -r /app/requirements.txt

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
#
# The user is created *before* the source copy on purpose: `COPY --chown`
# below carries ownership in the layer it already writes. A trailing
# `chown -R /app` instead rewrote the metadata of every file after the copy,
# which stored a second full copy of the tree in its own ~400 MB layer.
RUN useradd --uid 10001 --user-group --create-home --shell /usr/sbin/nologin plexichat \
    && mkdir -p /home/plexichat/.plexichat \
    && chown -R plexichat:plexichat /home/plexichat

COPY --chown=10001:10001 . /app

USER plexichat

EXPOSE 8000
EXPOSE 30000-30100/udp

HEALTHCHECK --interval=15s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -sf http://localhost:8000/api/v1/ready

CMD ["python", "main.py"]
