# Stock News Bot - Cloud Run Job image
#
# One image, many Cloud Run Jobs. Each job overrides the container's
# command/args to run a different script (news_bot.py, alert_bot.py,
# concall_bot.py, weekly_bot.py, earnings_engine/main.py calendar|detect).
# Cloud Scheduler triggers each job on its own cron schedule (see
# deploy/README_CLOUD_RUN.md).
#
# entrypoint.sh pulls persisted state (JSON state files, SQLite DB, PDF
# archive) down from a GCS bucket before running, and pushes it back up
# afterwards, since Cloud Run Jobs get a fresh, empty filesystem every run.

FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN chmod +x entrypoint.sh

ENV PYTHONUNBUFFERED=1 \
    TZ=Asia/Kolkata

ENTRYPOINT ["./entrypoint.sh"]
# Default command; every Cloud Run Job overrides this with its own args.
CMD ["python3", "news_bot.py"]
