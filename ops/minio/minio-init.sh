#!/bin/sh
# One-shot MinIO initialisation (idempotent): buckets, versioning, object lock, app credentials.
#  - fs-objects : every uploaded/produced file version (versioning ON; immutable by design, 06 §5)
#  - fs-finals  : FINAL_LOCKED files, created WITH object lock; default retention COMPLIANCE for the
#                 engagement retention period (7 years, ADR-0009) so nobody - not even an admin -
#                 can delete or overwrite a signed final (06 §1 WORM, 07 §2)
# POSIX sh (the mc image has no bash).
set -eu

: "${MINIO_ROOT_USER:?}" "${MINIO_ROOT_PASSWORD:?}" "${MINIO_APP_ACCESS_KEY:?}" "${MINIO_APP_SECRET_KEY:?}"
OBJECTS="${FSWEB_S3_BUCKET:-fs-objects}"
FINALS="${FS_FINALS_BUCKET:-fs-finals}"
RETENTION_DAYS="${FS_FINALS_RETENTION_DAYS:-2557}"

mc alias set fs http://minio:9000 "$MINIO_ROOT_USER" "$MINIO_ROOT_PASSWORD" >/dev/null

mc mb --ignore-existing "fs/$OBJECTS"
mc version enable "fs/$OBJECTS"
mc encrypt set sse-s3 "fs/$OBJECTS" >/dev/null 2>&1 || echo "warning: SSE-S3 default encryption not set (check MINIO_KMS_SECRET_KEY)"

if ! mc stat "fs/$FINALS" >/dev/null 2>&1; then
  mc mb --with-lock "fs/$FINALS"
fi
mc version enable "fs/$FINALS"
mc retention set --default COMPLIANCE "${RETENTION_DAYS}d" "fs/$FINALS"
mc encrypt set sse-s3 "fs/$FINALS" >/dev/null 2>&1 || true

# Least-privilege application user: object read/write on the two buckets, no bucket/admin rights.
cat > /tmp/fs-app-policy.json <<EOF
{
  "Version": "2012-10-17",
  "Statement": [
    {"Effect": "Allow",
     "Action": ["s3:GetObject", "s3:GetObjectVersion", "s3:PutObject", "s3:ListBucket", "s3:ListBucketVersions",
                "s3:GetObjectRetention", "s3:PutObjectRetention", "s3:GetObjectLegalHold", "s3:PutObjectLegalHold",
                "s3:GetBucketVersioning", "s3:GetBucketObjectLockConfiguration"],
     "Resource": ["arn:aws:s3:::$OBJECTS", "arn:aws:s3:::$OBJECTS/*", "arn:aws:s3:::$FINALS", "arn:aws:s3:::$FINALS/*"]}
  ]
}
EOF
mc admin policy create fs fs-app /tmp/fs-app-policy.json 2>/dev/null || mc admin policy update fs fs-app /tmp/fs-app-policy.json 2>/dev/null || true
mc admin user add fs "$MINIO_APP_ACCESS_KEY" "$MINIO_APP_SECRET_KEY" >/dev/null
mc admin policy attach fs fs-app --user "$MINIO_APP_ACCESS_KEY" >/dev/null 2>&1 || true
rm -f /tmp/fs-app-policy.json
echo "fs-platform: object storage ready ($OBJECTS versioned; $FINALS object-locked ${RETENTION_DAYS}d)"
