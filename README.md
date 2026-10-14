# AIAP Telegram Topic Moderator

This Cloudflare Worker moderates general text-only messages in selected Telegram forum topics. For the supplied topic, it only acts in chat `-1004419987380`, topic `263`.

It keeps messages containing a URL or text link, link preview, or supported content such as a photo, document, video, audio, voice message, sticker, poll, location, contact, or game. Other regular messages in the selected topic are deleted. Messages in other groups or topics, service messages, and bot messages are ignored.

After deleting a general message, the bot posts a short direction to use the [General chat](https://t.me/c/4419987380/1). A Cloudflare Queue schedules that notice for deletion after 8 seconds. The notice can remain slightly longer while the queue processes it.

## Requirements

- A Telegram bot created through [@BotFather](https://t.me/BotFather).
- The bot added to the AIAP supergroup as an administrator with **Delete messages** permission. Admin status lets it receive group messages needed for moderation.
- A Cloudflare account on the Workers Free plan. Cloudflare advertises free signup without a credit card.
- Node.js and npm for Wrangler, Cloudflare's deployment CLI.

Cloudflare Workers Free currently includes 100,000 requests per day. Cloudflare Queues Free includes 10,000 read, write, and delete operations per day. Each temporary notice typically uses three queue operations, so that allowance supports about 3,333 notice cleanups per day. Check current [Workers limits](https://developers.cloudflare.com/workers/platform/limits/) and [Queues pricing](https://developers.cloudflare.com/queues/platform/pricing/).

## Deploy

The Worker is already deployed. To connect your Telegram bot, run the Windows setup script from this repository. It prompts for the BotFather token with hidden input, saves the token and a generated webhook secret as Cloudflare secrets, then registers the webhook with Telegram.

```powershell
./scripts/setup-cloudflare.ps1
```

For a fresh deployment, Wrangler will open a browser so you can sign in to your Cloudflare account. Then run:

```sh
npx wrangler@latest login
npx wrangler@latest queues create topic-notice-cleanup
npx wrangler@latest secret put TELEGRAM_BOT_TOKEN
npx wrangler@latest secret put WEBHOOK_SECRET
npx wrangler@latest deploy
```

Wrangler prompts for each secret value. Do not commit bot tokens, webhook secrets, or `.dev.vars` to GitHub. Generate a webhook secret with:

```sh
openssl rand -hex 32
```

Save the generated value somewhere private, enter it when Wrangler prompts for `WEBHOOK_SECRET`, and use the same value to register the Telegram webhook. The Bot API accepts a secret made of letters, numbers, `_`, and `-`.

The deployment prints a `workers.dev` URL. Set that URL as Telegram's webhook, replacing the placeholders below. In Bash:

On Windows, the setup script above handles this step and removes its temporary secret file when finished.

```sh
export TELEGRAM_BOT_TOKEN="YOUR_BOT_TOKEN"
export WEBHOOK_SECRET="YOUR_WEBHOOK_SECRET"
export WORKER_URL="https://aiap-topic-moderator.YOUR_SUBDOMAIN.workers.dev"

curl --fail --request POST \
  "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/setWebhook" \
  --data-urlencode "url=${WORKER_URL}/telegram/webhook" \
  --data-urlencode "secret_token=${WEBHOOK_SECRET}" \
  --data-urlencode 'allowed_updates=["message","edited_message"]'
```

The webhook is protected by Telegram's `X-Telegram-Bot-Api-Secret-Token` header. Check the deployed Worker at `${WORKER_URL}/healthz`; it should return `{"ok":true}`.

## Add more topics

Edit `ALLOWED_TOPIC_IDS` in `wrangler.jsonc` to a comma-separated list such as `263,501,902`, then deploy again:

```sh
npx wrangler@latest deploy
```

The bot remains restricted to the chat ID in `ALLOWED_CHAT_ID`.

## Local development

Copy `.dev.vars.example` to `.dev.vars`, enter your bot token and a random webhook secret, then run:

```sh
npx wrangler@latest dev
```

Do not commit `.dev.vars`. Local queue behavior is simulated by Wrangler. To test against Telegram, expose the local Worker over HTTPS and register that URL as the webhook. Only one webhook can be active per bot token at a time.
