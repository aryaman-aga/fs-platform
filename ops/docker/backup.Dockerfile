# syntax=docker/dockerfile:1.7
# Backup tooling container (restic + scripts), run on demand by the host's systemd timers
# (ops/backup/systemd). Build context = repo root.
ARG DEBIAN_IMAGE=debian:trixie-slim
FROM ${DEBIAN_IMAGE}
RUN apt-get update \
 && apt-get install -y --no-install-recommends restic ca-certificates openssh-client curl jq \
 && rm -rf /var/lib/apt/lists/*
COPY ops/backup/*.sh /usr/local/bin/
RUN chmod 0755 /usr/local/bin/*.sh
ENTRYPOINT ["/usr/local/bin/restic-backup.sh"]
