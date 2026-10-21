# syntax=docker/dockerfile:1.7
# PostgreSQL 16 with pgBackRest for WAL archiving and backups (07 §2). Build context = repo root.
# Pin by digest in production: --build-arg POSTGRES_IMAGE=postgres:16-bookworm@sha256:<digest>
ARG POSTGRES_IMAGE=postgres:16-bookworm
FROM ${POSTGRES_IMAGE}
RUN apt-get update \
 && apt-get install -y --no-install-recommends pgbackrest \
 && rm -rf /var/lib/apt/lists/* \
 && mkdir -p /var/lib/pgbackrest /var/log/pgbackrest /var/spool/pgbackrest \
 && chown -R postgres:postgres /var/lib/pgbackrest /var/log/pgbackrest /var/spool/pgbackrest
COPY --chown=postgres:postgres ops/backup/pgbackrest.conf /etc/pgbackrest/pgbackrest.conf
