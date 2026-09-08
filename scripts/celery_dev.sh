#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

exec uv run celery -A app.core.celery_app worker -Q parsing,review --beat \
  --concurrency="${CELERY_CONCURRENCY:-2}" \
  --loglevel="${CELERY_LOGLEVEL:-INFO}"