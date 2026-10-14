# Telegram Topic Moderator

A small Dockerized Telegram bot that removes general text-only messages from configured forum topics while keeping messages that contain links or supported media. It receives updates through a Telegram webhook and is designed for Google Cloud Run.

## Current filter target

The supplied topic link is `https://t.me/c/4419987380/263`. Its forum topic ID is configured as `263`, and the Bot API chat ID is configured as `-1004419987380` (the `-100` Bot API prefix plus the private group ID in the link). Telegram documents forum topic links as message links whose topic ID is the topic creation service message ID. If `263` links to an ordinary message rather than the topic root, use the topic-root link or replace `263` with the correct topic ID.

The bot keeps a message if it contains a URL or text link, link preview, or supported content such as a photo, document, video, audio, voice message, sticker, poll, location, contact, or game. Other regular messages in the configured topic are deleted. Messages in other groups or topics are ignored. Service messages and messages from bots are ignored.

## Requirements

- A Telegram bot created through [@BotFather](https://t.me/BotFather).
- The bot added to the AIAP supergroup as an administrator with **Delete messages** permission. Admin status lets it receive group messages needed for moderation.
- A Google Cloud project with billing enabled and the Cloud Run, Cloud Build, Artifact Registry, and Secret Manager APIs enabled.
- `gcloud` CLI for deployment.

## Configure secrets

Create two secrets in Secret Manager. Do not commit bot tokens or webhook secrets to this repository.

```sh
gcloud secrets create telegram-bot-token --replication-policy=automatic
echo -n "$TELEGRAM_BOT_TOKEN" | gcloud secrets versions add telegram-bot-token --data-file=-

gcloud secrets create telegram-webhook-secret --replication-policy=automatic
echo -n "$WEBHOOK_SECRET" | gcloud secrets versions add telegram-webhook-secret --data-file=-
```

`WEBHOOK_SECRET` should be a random string using only letters, digits, `_`, and `-` (for example, generate one with `openssl rand -hex 32`).

Grant the Cloud Run runtime service account access to both secrets. In Bash:

```sh
PROJECT_ID="YOUR_PROJECT_ID"
PROJECT_NUMBER="$(gcloud projects describe "$PROJECT_ID" --format='value(projectNumber)')"
RUNTIME_SERVICE_ACCOUNT="${PROJECT_NUMBER}-compute@developer.gserviceaccount.com"

for SECRET in telegram-bot-token telegram-webhook-secret; do
  gcloud secrets add-iam-policy-binding "$SECRET" \
    --member="serviceAccount:${RUNTIME_SERVICE_ACCOUNT}" \
    --role="roles/secretmanager.secretAccessor"
done
```

## Deploy to Cloud Run

From this repository, choose a project and region, then deploy the Dockerfile-backed service:

```sh
gcloud config set project YOUR_PROJECT_ID
gcloud services enable run.googleapis.com cloudbuild.googleapis.com artifactregistry.googleapis.com secretmanager.googleapis.com

gcloud run deploy aiap-topic-moderator \
  --source . \
  --region asia-southeast1 \
  --service-account="$RUNTIME_SERVICE_ACCOUNT" \
  --allow-unauthenticated \
  --min 0 \
  --max 2 \
  --set-env-vars ALLOWED_CHAT_ID=-1004419987380,ALLOWED_TOPIC_IDS=263 \
  --set-secrets TELEGRAM_BOT_TOKEN=telegram-bot-token:latest,WEBHOOK_SECRET=telegram-webhook-secret:latest
```

Cloud Run scales to zero between webhook requests. `--min 0` avoids keeping an idle instance warm; usage can still incur charges if it exceeds the applicable free tier.

After deployment, copy the service URL shown by `gcloud run deploy` and register it as the Telegram webhook. In a Bash shell where `TELEGRAM_BOT_TOKEN` and `WEBHOOK_SECRET` are set:

```sh
export SERVICE_URL="https://YOUR_CLOUD_RUN_SERVICE_URL"
curl --fail --request POST \
  "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/setWebhook" \
  --data-urlencode "url=${SERVICE_URL}/telegram/webhook" \
  --data-urlencode "secret_token=${WEBHOOK_SECRET}" \
  --data-urlencode 'allowed_updates=["message","edited_message"]'
```

The service checks Telegram's `X-Telegram-Bot-Api-Secret-Token` header. A health endpoint is available at `${SERVICE_URL}/healthz`.

## Add more topics

Set `ALLOWED_TOPIC_IDS` to a comma-separated list, such as `263,501,902`. The configured group remains restricted by `ALLOWED_CHAT_ID`.

## Local Docker run

```sh
docker build -t aiap-topic-moderator .
docker run --rm -p 8080:8080 \
  -e TELEGRAM_BOT_TOKEN="$TELEGRAM_BOT_TOKEN" \
  -e WEBHOOK_SECRET="$WEBHOOK_SECRET" \
  -e ALLOWED_CHAT_ID=-1004419987380 \
  -e ALLOWED_TOPIC_IDS=263 \
  aiap-topic-moderator
```

For local webhook testing, expose port 8080 through an HTTPS tunnel and register that HTTPS URL with Telegram. Only one webhook can be active per bot token at a time.
