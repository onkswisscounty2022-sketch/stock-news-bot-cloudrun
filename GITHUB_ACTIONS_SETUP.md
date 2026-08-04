# GitHub Actions secrets required

The Cloud Run workflow authenticates with Workload Identity Federation; no JSON service-account key is stored in GitHub.

Add these repository secrets before the first push that should deploy:

- `DISCORD_WEBHOOK_URL`
- `WEBHOOK_EARNINGS`
- `WEBHOOK_CONCALL`
- `WEBHOOK_SMART_ALERTS`
- `WEBHOOK_WEEKLY_WRAP`
- `GMAIL_APP_PASSWORD`
- `GEMINI_API_KEY`
- `GMAIL_SENDER`
- `GMAIL_RECEIVER`

In GitHub: repository **Settings → Secrets and variables → Actions → New repository secret**.

The workflow is restricted to the `main` branch and uses the Google Cloud provider `github-actions` in `github-actions-pool`. It builds the image, creates or updates the Cloud Run Jobs, creates the private GCS state bucket, and creates or updates the Cloud Scheduler triggers.

Never commit `.env`, JSON credentials, SQLite data, downloaded PDFs, or webhook URLs to the repository.
