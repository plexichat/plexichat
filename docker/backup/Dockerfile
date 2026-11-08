FROM golang:1.24-alpine AS supercronic

# Build supercronic from its pinned Go module instead of GitHub release assets.
# GitHub's release host returned 503/504 on 2026-11-08 while the module proxy
# stayed healthy, blocking the backup image. The module proxy serves the same
# source and Go verifies it against the public sum database.
ARG SUPERCRONIC_VERSION=0.2.33
RUN CGO_ENABLED=0 go install \
    "github.com/aptible/supercronic@v${SUPERCRONIC_VERSION}"

FROM alpine:3.24

RUN apk add --no-cache \
    postgresql-client \
    restic \
    bash \
    tzdata \
    curl \
    gzip

COPY --from=supercronic /go/bin/supercronic /usr/local/bin/supercronic
RUN chmod +x /usr/local/bin/supercronic

COPY scripts/ /scripts/
RUN chmod +x /scripts/*.sh

VOLUME ["/repo", "/plexichat-home", "/rustfs-data"]

ENTRYPOINT ["/scripts/entrypoint.sh"]
