# Discord Fact Bot

Posts a random fact from the chat RAG API to a Discord channel. Two ways to run:

- **Cron / Railway scheduled job** (recommended): run `post_fact.py` on a schedule; no long-lived process.
- **Long-running bot**: run `fact_bot.py` for a persistent bot that posts on an interval and supports `!fact`.

**Deploy multiple instances or different channels:** use a [Railway template](https://docs.railway.com/guides/create). See **[RAILWAY_TEMPLATE.md](./RAILWAY_TEMPLATE.md)** for how to turn this into a one-click template so each deploy only needs `DISCORD_BOT_TOKEN`, `CHANNEL_ID`, and `CHAT_API_URL`.

## Setup

1. **Create a Discord bot**
   - Go to [Discord Developer Portal](https://discord.com/developers/applications) → New Application.
   - Open the **Bot** section → Add Bot → copy the **Token** (set `DISCORD_BOT_TOKEN`).
   - Under **Privileged Gateway Intents**, enable **Message Content Intent** if you want `!fact` to work.

2. **Invite the bot**
   - OAuth2 → URL Generator → scopes: `bot`; permissions: **Send Messages**, **Read Message History** (and **Read Messages/View Channels**).
   - Open the generated URL and add the bot to your server.

3. **Get the channel ID**
   - In Discord: User Settings → App Settings → Advanced → enable **Developer Mode**.
   - Right‑click the channel where the bot should post → **Copy Channel ID** (set `CHANNEL_ID`).

## Environment

Copy **`bot/.env.example`** to `.env` (or set in Railway Variables). Reference:

| Variable             | Required | Default                    | Description                          |
|----------------------|----------|----------------------------|--------------------------------------|
| `DISCORD_BOT_TOKEN`  | Yes      | —                          | Bot token from the Developer Portal. |
| `CHANNEL_ID`         | Yes      | —                          | Discord channel ID to post in.       |
| `CHAT_API_URL`       | No       | `http://127.0.0.1:8000`   | Base URL of the chat API.             |
| `INTERVAL_MINUTES`   | No       | `60`                       | Minutes between scheduled posts (fact_bot.py only). |
| `FACT_PROMPT`        | No       | `Give me a random fact`   | Prompt sent to the chat API.          |

## Run

### Option A: Cron / Railway scheduled job (recommended)

Use **`post_fact.py`** for a one-shot run: fetch one fact, post to Discord, then exit. Schedule it with cron or Railway’s cron.

**Local / one-off run** (from project root):

```bash
# Use bot/.env.example as a template; set vars in .env or inline:
uv run python bot/post_fact.py
```

**Railway (config as code)**

1. In the same project, create a **new service** from this repo (same repo as your chat API is fine).
2. **Settings** → **Config file path** → set to **`bot/railway.toml`**. That file defines:
   - **Build**: `uv sync`
   - **Start command**: `uv run python bot/post_fact.py` (this is what runs on each cron trigger)
   - **Cron schedule**: `0 * * * *` (every hour at :00 UTC). Edit `bot/railway.toml` to change (e.g. `0 */6 * * *` for every 6 hours).
3. **Variables**: Add the same vars as in **`bot/.env.example`** — at minimum **`DISCORD_BOT_TOKEN`**, **`CHANNEL_ID`**, and **`CHAT_API_URL`** (your chat API’s public URL, e.g. `https://your-chat-app.railway.app`).

No need to set start command or cron in the dashboard; `bot/railway.toml` does it. Only the three (or four) environment variables need to be set in Railway.

**Why cron instead of a loop**

- No process running 24/7; the job runs only when the schedule triggers.
- Fits Railway’s cron model: run a command on a schedule, then exit.
- Exit code: 0 = success, non-zero = failure (so you can alert on failures if you want).

### Option B: Long-running bot

For a persistent bot that posts on a timer and supports **`!fact`** in Discord:

```bash
uv run python bot/fact_bot.py
```

Or with env vars inline:

```bash
DISCORD_BOT_TOKEN=your_token CHANNEL_ID=123456789 uv run python bot/fact_bot.py
```

Behavior: on startup the bot connects to Discord, then every `INTERVAL_MINUTES` it fetches a fact and posts it. Users can type **`!fact`** in the channel to get a fact on demand.
