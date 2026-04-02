# gback-bot

Always-on Discord bot with two features:

1. **Scheduled facts** — Posts a random fact to a channel on a configurable interval (replaces the Railway cron approach)
2. **DM chat** — Users can DM the bot for a multi-turn conversation powered by your RAG chat API, with chat history stored in Postgres

## Architecture

```
Discord User (DM)
    │
    ▼
gback-bot (always-on, Railway)
    ├── Reads last N messages from Postgres
    ├── Sends message + history to RAG API
    ├── Stores user msg + bot response in Postgres
    │
    ▼
chat-rag-fast-api (Railway)
    ├── ChromaDB context retrieval
    └── OpenRouter LLM completion
```

---

## Setup

### 1. Discord Developer Portal

Go to [discord.com/developers/applications](https://discord.com/developers/applications) and select your bot application.

#### Bot settings (Bot → left sidebar):

- **Privileged Gateway Intents** — enable these:
  - ✅ **MESSAGE CONTENT INTENT** — required to read DM message text
  - ✅ **SERVER MEMBERS INTENT** — not strictly required but good to have
- Copy your **Bot Token** (you'll need it for `DISCORD_BOT_TOKEN`)

#### OAuth2 (OAuth2 → URL Generator):

If you need to re-invite the bot to a server, generate a URL with these scopes and permissions:
- **Scopes:** `bot`
- **Bot Permissions:**
  - Send Messages
  - Read Message History
  - Use Slash Commands (optional, for future use)

> **Note:** DMs work automatically — users can DM any bot that shares a server with them. No special permission needed for DM access.

### 2. Postgres on Railway

1. In your Railway project, click **+ New** → **Database** → **PostgreSQL**
2. Railway creates the database and exposes a `DATABASE_URL` variable
3. **Link it to your bot service:**
   - Go to your bot service → **Variables**
   - Click **+ Variable Reference** → select the Postgres service → `DATABASE_URL`
   - Or manually copy the connection string from the Postgres service's **Connect** tab

**That's it for Postgres.** The bot auto-creates the `chat_history` table and index on first startup. No manual SQL needed.

#### If you want to inspect the database manually:

Railway's Postgres service has a **Data** tab with a built-in query editor, or connect with any Postgres client:

```sql
-- See recent chat history
SELECT * FROM chat_history ORDER BY created_at DESC LIMIT 20;

-- Count messages per user
SELECT user_id, COUNT(*) FROM chat_history GROUP BY user_id;

-- Clear a specific user's history
DELETE FROM chat_history WHERE user_id = '123456789';

-- Nuclear option: clear everything
TRUNCATE chat_history;
```

### 3. Environment Variables

Set these in Railway (service → Variables) or in `.env` for local dev:

| Variable | Required | Description |
|---|---|---|
| `DISCORD_BOT_TOKEN` | ✅ | Bot token from Discord Developer Portal |
| `CHANNEL_ID` | ✅ | Channel ID for scheduled fact posts |
| `DATABASE_URL` | ✅ | Postgres connection string (auto-set by Railway if linked) |
| `CHAT_API_URL` | ✅ | Base URL of your FastAPI RAG service |
| `CHAT_API_SHOP` | ✅ | Shop identifier for the `/chat` endpoint |
| `CHAT_WIDGET_TOKEN` | ✅ | Widget token for the `/chat` endpoint |
| `FACT_INTERVAL_MINUTES` | ❌ | Minutes between fact posts (default: `480` = 8h) |
| `FACT_PROMPT` | ❌ | Prompt(s) for facts — string or JSON (default: `Give me a random fact`) |
| `CHAT_HISTORY_LIMIT` | ❌ | Message pairs to keep as context per user (default: `5`) |

See `.env.example` for full details and formats.

### 4. Deploy

#### Railway (recommended):

1. Push to your repo
2. Railway auto-deploys from `railway.toml`
3. The bot starts as a long-running process (not a cron)

> **Important:** If your service was previously configured as a **cron job** in Railway, you need to change it:
> - Go to service **Settings** → **Deploy** section
> - Remove the **Cron Schedule** (clear the field or toggle it off)
> - The `railway.toml` already has `startCommand = "python bot.py"` with no cron schedule

#### Local dev:

```bash
cp .env.example .env
# Fill in your values
pip install -r requirements.txt
python bot.py
```

### 5. Chat RAG API changes

The `/chat` endpoint on your FastAPI service now accepts an optional `history` field:

```json
{
  "shop": "gback",
  "token": "your-token",
  "session_id": "discord-123456",
  "message": "What colors does this come in?",
  "history": [
    {"role": "user", "content": "Tell me about the wool jacket"},
    {"role": "assistant", "content": "The wool jacket is a premium..."},
    {"role": "user", "content": "How much is it?"},
    {"role": "assistant", "content": "It's priced at $149.99..."}
  ]
}
```

The `history` field is optional — existing clients (web widget, etc.) continue to work without changes.

---

## Bot Commands

| Command | Where | Description |
|---|---|---|
| `!fact` | Any channel/DM | Manually fetch a random fact |
| `!clear` | DM only | Clear your chat history with the bot |

---

## How DM Chat Works

1. User sends a DM to the bot
2. Bot fetches the last `CHAT_HISTORY_LIMIT` message pairs from Postgres
3. Sends the message + history to your `/chat` endpoint
4. The RAG API retrieves context from ChromaDB and calls OpenRouter with the full conversation
5. Bot stores both the user message and assistant response in Postgres
6. Sends the response back to the user

History is per-user (keyed by Discord user ID). Users can clear their history with `!clear`.

---

## Files

| File | Purpose |
|---|---|
| `bot.py` | Main bot — DM handler, fact scheduler, commands |
| `db.py` | Postgres chat history (asyncpg) — auto-creates tables |
| `post_fact.py` | Legacy one-shot cron script (kept for reference, no longer used) |
| `fact_bot.py` | Legacy long-running bot (kept for reference, replaced by `bot.py`) |
| `railway.toml` | Railway deployment config |
