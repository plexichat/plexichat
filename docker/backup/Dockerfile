FROM alpine:3.20

RUN apk add --no-cache \
    postgresql-client \
    restic \
    bash \
    tzdata \
    curl \
    gzip

ARG SUPERCRONIC_VERSION=0.2.33
RUN curl -fsSL \
    "https://github.com/aptible/supercronic/releases/download/v${SUPERCRONIC_VERSION}/supercronic-linux-amd64" \
    -o /usr/local/bin/supercronic \
    && chmod +x /usr/local/bin/supercronic

COPY scripts/ /scripts/
RUN chmod +x /scripts/*.sh

VOLUME ["/repo", "/plexichat-home", "/minio-data"]

ENTRYPOINT ["/scripts/entrypoint.sh"]
