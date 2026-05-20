#!/usr/bin/env bash
set -euo pipefail

SERVICE_NAME="${SERVICE_NAME:-avant-s2dr4}"
REGION="${REGION:-us-central1}"
MEMORY="${MEMORY:-4Gi}"
CPU="${CPU:-2}"
MAX_INSTANCES="${MAX_INSTANCES:-1}"
CONCURRENCY="${CONCURRENCY:-1}"
TIMEOUT="${TIMEOUT:-3600}"

echo "Updating Cloud Run service ${SERVICE_NAME} in ${REGION}"
echo "memory=${MEMORY} cpu=${CPU} max-instances=${MAX_INSTANCES} concurrency=${CONCURRENCY} timeout=${TIMEOUT}"

gcloud run services update "${SERVICE_NAME}" \
  --region "${REGION}" \
  --memory "${MEMORY}" \
  --cpu "${CPU}" \
  --max-instances "${MAX_INSTANCES}" \
  --concurrency "${CONCURRENCY}" \
  --timeout "${TIMEOUT}" \
  --no-cpu-throttling
