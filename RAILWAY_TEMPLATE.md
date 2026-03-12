# Railway template: Discord Fact Cron

Turn this bot into a **Railway template** so you (or others) can deploy multiple instances with one click—e.g. one cron per Discord channel or server—by filling in three variables each time.

## One-time: create the template

1. **Deploy the bot once** (so Railway has a project to copy):
   - In Railway, **New Project** → **Deploy from GitHub repo** → select this repo.
   - **Add a service** (or use the one created). In the service:
     - **Settings** → **Config file path** → **`bot/railway.toml`**.
     - **Variables** → add **`DISCORD_BOT_TOKEN`**, **`CHANNEL_ID`**, **`CHAT_API_URL`** (use real or placeholder values for now).
   - Deploy. Once it’s green, you’re ready to capture it as a template.

2. **Generate template from project**
   - Open the **project** (the one with the fact-cron service).
   - **Project Settings** (gear or right-hand menu) → scroll to **Generate Template from Project**.
   - Click it. You’ll be taken to the template composer.

3. **Configure the template**
   - **Service source**: should be this GitHub repo (same branch you want people to deploy from).
   - **Config file path**: **`bot/railway.toml`** (so build, start command, and cron schedule come from code).
   - **Variables**: make these **required** so every deploy prompts for them:
     - **`DISCORD_BOT_TOKEN`** (required)
     - **`CHANNEL_ID`** (required)
     - **`CHAT_API_URL`** (required)
   - Optional: add **`FACT_PROMPT`** as an optional variable.
   - **Root Directory**: leave blank (repo root) so `uv sync` and `bot/post_fact.py` work.
   - Save / **Create Template**.

4. **Share the template**
   - Copy the **template URL** from the template page (e.g. `https://railway.com/template/...`).
   - You can also **Publish** it to the [Railway template marketplace](https://railway.com/templates) if you want it to be public and discoverable.

## Deploying more instances (different channels / servers)

- **From the template URL**: open the link → **Deploy** → fill in **DISCORD_BOT_TOKEN**, **CHANNEL_ID**, **CHAT_API_URL** (and **CHAT_API_URL** to your chat API) → Deploy. Each deploy is a new project with one cron service.
- **From the dashboard**: **New** → **Deploy from Template** → choose your template → same flow.

Use a **different Discord bot token and/or CHANNEL_ID** per deploy to post to different servers or channels. **CHAT_API_URL** can be the same (your single chat API) or different if you have multiple backends.

## What the template includes

- One **cron service** that runs `uv run python bot/post_fact.py` on the schedule in **`bot/railway.toml`** (default: hourly).
- Build from repo root with **`uv sync`**; no extra start command or cron config needed in the UI when **Config file path** is **`bot/railway.toml`**.

## Optional: publish to the marketplace

From your template page you can **Publish** so it appears on [railway.com/templates](https://railway.com/templates). You can earn [kickbacks](https://docs.railway.com/templates/kickbacks) for usage. In the template description, point users to **`bot/README.md`** and **`bot/.env.example`** for Discord setup (create bot, invite, get channel ID).
