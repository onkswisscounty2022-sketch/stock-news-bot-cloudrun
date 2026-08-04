#!/usr/bin/env bash
#
# Stock News Bot - one-shot Cloud Run deployment.
#
# Does everything README_CLOUD_RUN.md describes manually: enables APIs,
# creates the state bucket, secrets, service accounts, builds+pushes one
# image, creates all 5 Cloud Run Jobs, and wires up every Cloud Scheduler
# trigger (mirrors the old crontab exactly). Safe to re-run - every step
# is idempotent.
#
# Run this from Cloud Shell (https://shell.cloud.google.com) or any machine
# with `gcloud` installed and authenticated (`gcloud auth login`).
#
# Usage:
#   cd stock_news_bot
#   bash deploy/deploy.sh
#
set -euo pipefail

# ─── Config - edit these or export as env vars before running ──────────────
PROJECT_ID="${PROJECT_ID:-$(gcloud config get-value project 2>/dev/null)}"
REGION="${REGION:-asia-south1}"
REPO="${REPO:-stock-news-bot-repo}"
IMAGE_NAME="${IMAGE_NAME:-stock-news-bot}"
SA_NAME="${SA_NAME:-stock-news-bot-sa}"
INVOKER_SA_NAME="${INVOKER_SA_NAME:-stock-news-bot-invoker}"
BUCKET="${BUCKET:-${PROJECT_ID}-stock-news-bot-state}"
TIME_ZONE="${TIME_ZONE:-Asia/Kolkata}"

if [[ -z "$PROJECT_ID" ]]; then
  echo "ERROR: no project set. Run: gcloud config set project YOUR_PROJECT_ID"
  exit 1
fi

echo "== Deploying Stock News Bot to Cloud Run =="
echo "Project: $PROJECT_ID | Region: $REGION | Bucket: $BUCKET"
echo

gcloud config set project "$PROJECT_ID" >/dev/null

# ─── 0. Self-heal: some orgs disable automatic IAM grants for default
# service agents (secure-by-default policy). Cloud Build then can't read
# its own upload from the staging bucket. The deployer already holds
# Project IAM Admin, so it can grant this itself - no manual console step.
PROJECT_NUMBER="$(gcloud projects describe "$PROJECT_ID" --format='value(projectNumber)')"
COMPUTE_SA="${PROJECT_NUMBER}-compute@developer.gserviceaccount.com"
gcloud projects add-iam-policy-binding "$PROJECT_ID" \
  --member="serviceAccount:${COMPUTE_SA}" --role="roles/storage.objectViewer" --quiet >/dev/null
gcloud projects add-iam-policy-binding "$PROJECT_ID" \
  --member="serviceAccount:${COMPUTE_SA}" --role="roles/logging.logWriter" --quiet >/dev/null

# ─── 1. Enable required APIs ────────────────────────────────────────────────
echo "-- Enabling APIs --"
gcloud services enable run.googleapis.com artifactregistry.googleapis.com \
  cloudscheduler.googleapis.com cloudbuild.googleapis.com \
  storage.googleapis.com bigquery.googleapis.com \
  secretmanager.googleapis.com logging.googleapis.com --quiet

# ─── 2. State bucket (replaces the VM's persistent disk) ──────────────────
echo "-- State bucket: gs://$BUCKET --"
if ! gsutil ls -b "gs://$BUCKET" >/dev/null 2>&1; then
  gsutil mb -l "$REGION" "gs://$BUCKET"
else
  echo "   already exists, skipping create"
fi

# ─── 3. Secrets - prompt only if missing ────────────────────────────────────
echo "-- Secrets --"
ensure_secret() {
  local name="$1" prompt="$2" env_name="$3"
  local val="${!env_name:-}"
  if [[ -z "$val" ]]; then
    read -rsp "   Enter value for secret '$name' ($prompt): " val
    echo
  else
    echo "   using ${env_name} from the deployment environment"
  fi
  if gcloud secrets describe "$name" >/dev/null 2>&1; then
    printf '%s' "$val" | gcloud secrets versions add "$name" --data-file=- >/dev/null
    echo "   added a new version to $name"
  else
    printf '%s' "$val" | gcloud secrets create "$name" --data-file=- >/dev/null
    echo "   created $name"
  fi
}
ensure_secret "discord-webhook-url"  "#stocks-news webhook" DISCORD_WEBHOOK_URL
ensure_secret "webhook-earnings"     "#earning-alerts webhook" WEBHOOK_EARNINGS
ensure_secret "webhook-concall"      "#concall-intel webhook" WEBHOOK_CONCALL
ensure_secret "webhook-smart-alerts" "#smart-alerts webhook" WEBHOOK_SMART_ALERTS
ensure_secret "webhook-weekly-wrap"  "#weekly-wrap webhook" WEBHOOK_WEEKLY_WRAP
ensure_secret "gmail-app-password"   "Gmail app password" GMAIL_APP_PASSWORD
ensure_secret "gemini-api-key"       "Gemini API key" GEMINI_API_KEY

echo
GMAIL_SENDER="${GMAIL_SENDER:-}"
GMAIL_RECEIVER="${GMAIL_RECEIVER:-}"
if [[ -z "$GMAIL_SENDER" || -z "$GMAIL_RECEIVER" ]]; then
  read -rp "Gmail sender address (e.g. you@gmail.com): " GMAIL_SENDER
  read -rp "Gmail receiver address (e.g. you@gmail.com): " GMAIL_RECEIVER
fi

# ─── 4. Service accounts ────────────────────────────────────────────────────
SA_EMAIL="${SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"
echo "-- Service account: $SA_EMAIL --"
if ! gcloud iam service-accounts describe "$SA_EMAIL" >/dev/null 2>&1; then
  gcloud iam service-accounts create "$SA_NAME" --display-name="Stock News Bot Cloud Run runner"
else
  echo "   already exists, skipping create"
fi
for role in roles/secretmanager.secretAccessor roles/logging.logWriter \
            roles/storage.objectAdmin roles/bigquery.dataViewer roles/bigquery.jobUser; do
  gcloud projects add-iam-policy-binding "$PROJECT_ID" \
    --member="serviceAccount:${SA_EMAIL}" --role="${role}" --quiet >/dev/null
done

INVOKER_EMAIL="${INVOKER_SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"
echo "-- Scheduler invoker: $INVOKER_EMAIL --"
if ! gcloud iam service-accounts describe "$INVOKER_EMAIL" >/dev/null 2>&1; then
  gcloud iam service-accounts create "$INVOKER_SA_NAME" --display-name="Stock News Bot Scheduler invoker"
else
  echo "   already exists, skipping create"
fi
gcloud projects add-iam-policy-binding "$PROJECT_ID" \
  --member="serviceAccount:${INVOKER_EMAIL}" --role="roles/run.invoker" --quiet >/dev/null

# ─── 5. Artifact Registry + build/push one image for all jobs ─────────────
echo "-- Artifact Registry repo: $REPO --"
if ! gcloud artifacts repositories describe "$REPO" --location="$REGION" >/dev/null 2>&1; then
  gcloud artifacts repositories create "$REPO" \
    --repository-format=docker --location="$REGION"
else
  echo "   already exists, skipping create"
fi

IMAGE="${REGION}-docker.pkg.dev/${PROJECT_ID}/${REPO}/${IMAGE_NAME}:latest"
echo "-- Building image: $IMAGE --"
gcloud builds submit --tag "$IMAGE" "$(dirname "$0")/.."

# ─── 6. Create/update the 5 Cloud Run Jobs ─────────────────────────────────
COMMON_ENV="STATE_BUCKET=${BUCKET},GMAIL_SENDER=${GMAIL_SENDER},GMAIL_RECEIVER=${GMAIL_RECEIVER}"
COMMON_SECRETS="DISCORD_WEBHOOK_URL=discord-webhook-url:latest,WEBHOOK_EARNINGS=webhook-earnings:latest,WEBHOOK_CONCALL=webhook-concall:latest,WEBHOOK_SMART_ALERTS=webhook-smart-alerts:latest,WEBHOOK_WEEKLY_WRAP=webhook-weekly-wrap:latest,GMAIL_APP_PASSWORD=gmail-app-password:latest,GEMINI_API_KEY=gemini-api-key:latest"

create_or_update_job() {
  local job_name="$1" args="$2" timeout="$3" memory="$4" extra_env="${5:-}"
  local env_vars="$COMMON_ENV"
  [[ -n "$extra_env" ]] && env_vars="${env_vars},${extra_env}"

  echo "-- Cloud Run Job: $job_name --"
  if gcloud run jobs describe "$job_name" --region="$REGION" >/dev/null 2>&1; then
    gcloud run jobs update "$job_name" \
      --image="$IMAGE" --region="$REGION" --service-account="$SA_EMAIL" \
      --set-env-vars="$env_vars" --set-secrets="$COMMON_SECRETS" \
      --command=python3 --args="$args" \
      --max-retries=0 --task-timeout="$timeout" --memory="$memory"
  else
    gcloud run jobs create "$job_name" \
      --image="$IMAGE" --region="$REGION" --service-account="$SA_EMAIL" \
      --set-env-vars="$env_vars" --set-secrets="$COMMON_SECRETS" \
      --command=python3 --args="$args" \
      --max-retries=0 --task-timeout="$timeout" --memory="$memory"
  fi
}

create_or_update_job "news-bot-digest"          "news_bot.py"                600 512Mi
create_or_update_job "news-bot-alerts"          "alert_bot.py"               300 512Mi
create_or_update_job "news-bot-concall"         "concall_bot.py"             600 512Mi
create_or_update_job "news-bot-weekly"          "weekly_bot.py"              600 512Mi
create_or_update_job "earnings-engine-calendar" "earnings_engine/main.py,calendar" 900 1Gi "GCP_PROJECT_ID=${PROJECT_ID}"
create_or_update_job "earnings-engine-detect"   "earnings_engine/main.py,detect"   900 1Gi "GCP_PROJECT_ID=${PROJECT_ID}"

# ─── 7. Cloud Scheduler triggers - mirrors the old crontab exactly ─────────
run_url() {
  echo "https://${REGION}-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/${PROJECT_ID}/jobs/$1:run"
}

create_or_update_schedule() {
  local sched_name="$1" job_name="$2" cron="$3"
  echo "-- Cloud Scheduler: $sched_name ($cron $TIME_ZONE) --"
  if gcloud scheduler jobs describe "$sched_name" --location="$REGION" >/dev/null 2>&1; then
    gcloud scheduler jobs update http "$sched_name" \
      --location="$REGION" --schedule="$cron" --time-zone="$TIME_ZONE" \
      --uri="$(run_url "$job_name")" --http-method=POST \
      --oauth-service-account-email="$INVOKER_EMAIL"
  else
    gcloud scheduler jobs create http "$sched_name" \
      --location="$REGION" --schedule="$cron" --time-zone="$TIME_ZONE" \
      --uri="$(run_url "$job_name")" --http-method=POST \
      --oauth-service-account-email="$INVOKER_EMAIL"
  fi
}

create_or_update_schedule "news-bot-digest-8am"    "news-bot-digest"  "0 8 * * *"
create_or_update_schedule "news-bot-digest-2pm"    "news-bot-digest"  "0 14 * * *"
create_or_update_schedule "news-bot-digest-6pm"    "news-bot-digest"  "0 18 * * *"
create_or_update_schedule "news-bot-alerts-30min"  "news-bot-alerts"  "*/30 * * * *"
create_or_update_schedule "news-bot-concall-630pm" "news-bot-concall" "30 18 * * *"
create_or_update_schedule "news-bot-concall-9pm"   "news-bot-concall" "0 21 * * *"
create_or_update_schedule "news-bot-weekly-sun"    "news-bot-weekly"  "30 8 * * 0"
create_or_update_schedule "earnings-calendar-7pm"  "earnings-engine-calendar" "0 19 * * *"
create_or_update_schedule "earnings-detect-130pm"  "earnings-engine-detect"   "30 13 * * *"
create_or_update_schedule "earnings-detect-430pm"  "earnings-engine-detect"   "30 16 * * *"
create_or_update_schedule "earnings-detect-830pm"  "earnings-engine-detect"   "30 20 * * *"

echo
echo "== Done =="
echo "Run one job manually to test:  gcloud run jobs execute news-bot-digest --region=$REGION"
echo "All 5 jobs are now scheduled to mirror the old crontab exactly."
echo
echo "NOTE: rotate the 3 Discord webhook URLs that were previously hardcoded"
echo "in config.py (WEBHOOK_EARNINGS, WEBHOOK_CONCALL, WEBHOOK_SMART_ALERTS,"
echo "WEBHOOK_WEEKLY_WRAP) before going live if you haven't already."
