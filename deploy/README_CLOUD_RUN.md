# Deploying Stock News Bot to Cloud Run (Jobs)

This replaces the Compute Engine VM + cron setup (`setup.sh`,
`setup_cron.sh`) with **one Docker image** and **five Cloud Run Jobs**
(one per script), each triggered by its own Cloud Scheduler entry that
mirrors the old crontab. No VM to keep patched/paying for 24/7.

Replace `YOUR_PROJECT_ID` and `YOUR_BUCKET` throughout.

## Quick path: one script does all of this

Nothing here deploys itself just by existing in the repo - Cloud Run has
no persistent server to `scp` files onto like the VM did. Each resource
(bucket, secrets, service accounts, image, 5 jobs, 11 scheduler triggers)
has to be created once. `deploy/deploy.sh` does all of it in a single run
and is safe to re-run:

```bash
# From Cloud Shell (https://shell.cloud.google.com) or any machine with
# gcloud installed and authenticated:
gcloud config set project YOUR_PROJECT_ID
cd stock_news_bot
bash deploy/deploy.sh
```

It prompts once for each secret that doesn't already exist (5 webhook
URLs, Gmail app password, Gemini API key) plus your Gmail sender/receiver
addresses, then builds the image and wires up everything else with no
further manual steps. Read on for what it's doing under the hood, or to
run/adjust individual steps.

## Why this needed code changes (unlike the VCP project)

Cloud Run Jobs get a **fresh, empty filesystem on every execution** — the
old VM kept state on disk permanently between cron runs. Three things had
to change to make this safe:

1. **Dedup state files** (`alert_state.json`, `concall_state.json`) and the
   **earnings SQLite DB + PDF archive** (`earnings_engine/`) now round-trip
   through a GCS bucket via `gcs_sync.py`, wired in through
   `entrypoint.sh`. Without this, every run would think everything is
   "new" and you'd get duplicate/repeat alerts.
2. `STATE_FILE` paths in `alert_bot.py` and `concall_bot.py` were hardcoded
   to `/home/onkswisscounty2022/...` — changed to a path relative to the
   script's own directory so they work identically on the VM, locally, and
   in the container.
3. `config.py` had **three Discord webhook URLs hardcoded directly in
   source** (`WEBHOOK_EARNINGS`, `WEBHOOK_CONCALL`, `WEBHOOK_SMART_ALERTS`,
   `WEBHOOK_WEEKLY_WRAP`). These now come from environment variables like
   the fourth one always did. **Rotate those webhook URLs in Discord** —
   treat them as compromised since they were committed to git history.

## 1. Enable APIs

```bash
gcloud config set project YOUR_PROJECT_ID
gcloud services enable run.googleapis.com artifactregistry.googleapis.com \
  cloudscheduler.googleapis.com cloudbuild.googleapis.com \
  storage.googleapis.com bigquery.googleapis.com \
  secretmanager.googleapis.com logging.googleapis.com
```

## 2. Create the state bucket (replaces the VM's persistent disk)

```bash
gsutil mb -l asia-south1 gs://YOUR_BUCKET
```

## 3. Store secrets in Secret Manager

```bash
for name in discord-webhook-url webhook-earnings webhook-concall \
            webhook-smart-alerts webhook-weekly-wrap gmail-app-password \
            gemini-api-key; do
  echo "-- create secret: $name --"
done

printf '%s' "https://discord.com/api/webhooks/.../..." | gcloud secrets create discord-webhook-url --data-file=-
printf '%s' "https://discord.com/api/webhooks/.../..." | gcloud secrets create webhook-earnings --data-file=-
printf '%s' "https://discord.com/api/webhooks/.../..." | gcloud secrets create webhook-concall --data-file=-
printf '%s' "https://discord.com/api/webhooks/.../..." | gcloud secrets create webhook-smart-alerts --data-file=-
printf '%s' "https://discord.com/api/webhooks/.../..." | gcloud secrets create webhook-weekly-wrap --data-file=-
printf '%s' "YOUR_GMAIL_APP_PASSWORD" | gcloud secrets create gmail-app-password --data-file=-
printf '%s' "YOUR_GEMINI_API_KEY" | gcloud secrets create gemini-api-key --data-file=-
```

## 4. Service account

```bash
gcloud iam service-accounts create stock-news-bot-sa

SA="stock-news-bot-sa@YOUR_PROJECT_ID.iam.gserviceaccount.com"

for role in roles/secretmanager.secretAccessor roles/logging.logWriter \
            roles/storage.objectAdmin roles/bigquery.dataViewer \
            roles/bigquery.jobUser; do
  gcloud projects add-iam-policy-binding YOUR_PROJECT_ID \
    --member="serviceAccount:${SA}" --role="${role}"
done
```

`roles/storage.objectAdmin` is scoped broadly here for simplicity; for
tighter security, create a custom role limited to `YOUR_BUCKET` only.

## 5. Build and push the image (one image, five jobs)

```bash
gcloud artifacts repositories create stock-news-bot-repo \
  --repository-format=docker --location=asia-south1

gcloud builds submit --tag \
  asia-south1-docker.pkg.dev/YOUR_PROJECT_ID/stock-news-bot-repo/stock-news-bot:latest .
```

## 6. Create the five Cloud Run Jobs

Common env vars every job needs (secrets are pulled in via
`--set-secrets`):

```bash
IMAGE=asia-south1-docker.pkg.dev/YOUR_PROJECT_ID/stock-news-bot-repo/stock-news-bot:latest
COMMON_SECRETS="DISCORD_WEBHOOK_URL=discord-webhook-url:latest,WEBHOOK_EARNINGS=webhook-earnings:latest,WEBHOOK_CONCALL=webhook-concall:latest,WEBHOOK_SMART_ALERTS=webhook-smart-alerts:latest,WEBHOOK_WEEKLY_WRAP=webhook-weekly-wrap:latest,GMAIL_APP_PASSWORD=gmail-app-password:latest,GEMINI_API_KEY=gemini-api-key:latest"
COMMON_ENV="STATE_BUCKET=YOUR_BUCKET,GMAIL_SENDER=your_gmail@gmail.com,GMAIL_RECEIVER=your_gmail@gmail.com"

# 1) Digest bot - 8 AM / 2 PM / 6 PM IST
gcloud run jobs create news-bot-digest \
  --image="$IMAGE" --region=asia-south1 --service-account="${SA}" \
  --set-env-vars="$COMMON_ENV" --set-secrets="$COMMON_SECRETS" \
  --command=python3 --args=news_bot.py \
  --max-retries=0 --task-timeout=600 --memory=512Mi

# 2) Smart alerts - every 30 min
gcloud run jobs create news-bot-alerts \
  --image="$IMAGE" --region=asia-south1 --service-account="${SA}" \
  --set-env-vars="$COMMON_ENV" --set-secrets="$COMMON_SECRETS" \
  --command=python3 --args=alert_bot.py \
  --max-retries=0 --task-timeout=300 --memory=512Mi

# 3) Concall intel - 6:30 PM and 9 PM IST
gcloud run jobs create news-bot-concall \
  --image="$IMAGE" --region=asia-south1 --service-account="${SA}" \
  --set-env-vars="$COMMON_ENV" --set-secrets="$COMMON_SECRETS" \
  --command=python3 --args=concall_bot.py \
  --max-retries=0 --task-timeout=600 --memory=512Mi

# 4) Weekly wrap - Sunday 8:30 AM IST
gcloud run jobs create news-bot-weekly \
  --image="$IMAGE" --region=asia-south1 --service-account="${SA}" \
  --set-env-vars="$COMMON_ENV" --set-secrets="$COMMON_SECRETS" \
  --command=python3 --args=weekly_bot.py \
  --max-retries=0 --task-timeout=600 --memory=512Mi

# 5) Earnings engine - calendar (7 PM IST) and detect (1:30/4:30/8:30 PM IST)
gcloud run jobs create earnings-engine-calendar \
  --image="$IMAGE" --region=asia-south1 --service-account="${SA}" \
  --set-env-vars="$COMMON_ENV,GCP_PROJECT_ID=YOUR_PROJECT_ID" --set-secrets="$COMMON_SECRETS" \
  --command=python3 --args="earnings_engine/main.py,calendar" \
  --max-retries=0 --task-timeout=900 --memory=1Gi

gcloud run jobs create earnings-engine-detect \
  --image="$IMAGE" --region=asia-south1 --service-account="${SA}" \
  --set-env-vars="$COMMON_ENV,GCP_PROJECT_ID=YOUR_PROJECT_ID" --set-secrets="$COMMON_SECRETS" \
  --command=python3 --args="earnings_engine/main.py,detect" \
  --max-retries=0 --task-timeout=900 --memory=1Gi
```

`--max-retries=0` matters here: these are stateful dedup jobs, and a retry
could re-post duplicate alerts if the first attempt partially succeeded.
Let Cloud Scheduler's next scheduled run pick up anything missed instead.

## 7. Cloud Scheduler — mirrors the original crontab exactly

```bash
gcloud iam service-accounts create stock-news-bot-invoker
INVOKER="stock-news-bot-invoker@YOUR_PROJECT_ID.iam.gserviceaccount.com"
gcloud projects add-iam-policy-binding YOUR_PROJECT_ID \
  --member="serviceAccount:${INVOKER}" --role="roles/run.invoker"

run_url() {
  echo "https://asia-south1-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/YOUR_PROJECT_ID/jobs/$1:run"
}

# 8 AM, 2 PM, 6 PM IST digest
gcloud scheduler jobs create http news-bot-digest-8am --location=asia-south1 \
  --schedule="0 8 * * *" --time-zone="Asia/Kolkata" \
  --uri="$(run_url news-bot-digest)" --http-method=POST --oauth-service-account-email="${INVOKER}"
gcloud scheduler jobs create http news-bot-digest-2pm --location=asia-south1 \
  --schedule="0 14 * * *" --time-zone="Asia/Kolkata" \
  --uri="$(run_url news-bot-digest)" --http-method=POST --oauth-service-account-email="${INVOKER}"
gcloud scheduler jobs create http news-bot-digest-6pm --location=asia-south1 \
  --schedule="0 18 * * *" --time-zone="Asia/Kolkata" \
  --uri="$(run_url news-bot-digest)" --http-method=POST --oauth-service-account-email="${INVOKER}"

# Every 30 min
gcloud scheduler jobs create http news-bot-alerts-30min --location=asia-south1 \
  --schedule="*/30 * * * *" --time-zone="Asia/Kolkata" \
  --uri="$(run_url news-bot-alerts)" --http-method=POST --oauth-service-account-email="${INVOKER}"

# Concall: 6:30 PM and 9:00 PM IST
gcloud scheduler jobs create http news-bot-concall-630pm --location=asia-south1 \
  --schedule="30 18 * * *" --time-zone="Asia/Kolkata" \
  --uri="$(run_url news-bot-concall)" --http-method=POST --oauth-service-account-email="${INVOKER}"
gcloud scheduler jobs create http news-bot-concall-9pm --location=asia-south1 \
  --schedule="0 21 * * *" --time-zone="Asia/Kolkata" \
  --uri="$(run_url news-bot-concall)" --http-method=POST --oauth-service-account-email="${INVOKER}"

# Weekly: Sunday 8:30 AM IST
gcloud scheduler jobs create http news-bot-weekly-sun --location=asia-south1 \
  --schedule="30 8 * * 0" --time-zone="Asia/Kolkata" \
  --uri="$(run_url news-bot-weekly)" --http-method=POST --oauth-service-account-email="${INVOKER}"

# Earnings calendar: 7 PM IST
gcloud scheduler jobs create http earnings-calendar-7pm --location=asia-south1 \
  --schedule="0 19 * * *" --time-zone="Asia/Kolkata" \
  --uri="$(run_url earnings-engine-calendar)" --http-method=POST --oauth-service-account-email="${INVOKER}"

# Earnings detection: 1:30 PM, 4:30 PM, 8:30 PM IST
gcloud scheduler jobs create http earnings-detect-130pm --location=asia-south1 \
  --schedule="30 13 * * *" --time-zone="Asia/Kolkata" \
  --uri="$(run_url earnings-engine-detect)" --http-method=POST --oauth-service-account-email="${INVOKER}"
gcloud scheduler jobs create http earnings-detect-430pm --location=asia-south1 \
  --schedule="30 16 * * *" --time-zone="Asia/Kolkata" \
  --uri="$(run_url earnings-engine-detect)" --http-method=POST --oauth-service-account-email="${INVOKER}"
gcloud scheduler jobs create http earnings-detect-830pm --location=asia-south1 \
  --schedule="30 20 * * *" --time-zone="Asia/Kolkata" \
  --uri="$(run_url earnings-engine-detect)" --http-method=POST --oauth-service-account-email="${INVOKER}"
```

Times are specified directly in `Asia/Kolkata`, so no UTC math like the
old crontab needed.

## 8. Validate

```bash
# Run one job manually and tail logs
gcloud run jobs execute news-bot-digest --region=asia-south1
gcloud beta run jobs executions logs read \
  $(gcloud run jobs executions list --job=news-bot-digest --region=asia-south1 \
      --limit=1 --format="value(name)") --region=asia-south1

# Confirm state persisted to GCS after a run
gsutil ls gs://YOUR_BUCKET/
```

## Security notes

- All credentials (Discord webhooks, Gmail app password, Gemini API key)
  now come from Secret Manager, not source code.
- **Rotate the three previously hardcoded Discord webhook URLs** before or
  right after this migration.
- The bucket only needs to be reachable by the job's service account —
  keep it private (default `gsutil mb` behavior).

## Cost

Five small, short-lived jobs running a few times a day/every 30 minutes
cost only for the seconds they execute — no idle VM charge. GCS storage
for a SQLite DB + PDF archive of this size is a negligible few cents/month.
