"""
Postgres chat history storage using asyncpg.

Table schema (auto-created on init):

    chat_history
    ├── id          SERIAL PRIMARY KEY
    ├── user_id     TEXT NOT NULL        -- Discord user ID
    ├── role        TEXT NOT NULL        -- 'user' or 'assistant'
    ├── content     TEXT NOT NULL        -- message text
    └── created_at  TIMESTAMPTZ         -- auto-set on insert
"""

import logging
from datetime import datetime, timezone

import asyncpg

logger = logging.getLogger(__name__)

CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS chat_history (
    id          SERIAL PRIMARY KEY,
    user_id     TEXT NOT NULL,
    role        TEXT NOT NULL,
    content     TEXT NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_chat_history_user_id
    ON chat_history (user_id, created_at DESC);
"""


class Database:
    """Async Postgres wrapper for chat history."""

    def __init__(self, dsn: str):
        self.dsn = dsn
        self.pool: asyncpg.Pool | None = None

    async def init(self):
        """Create connection pool and ensure table exists."""
        self.pool = await asyncpg.create_pool(self.dsn, min_size=1, max_size=5)
        async with self.pool.acquire() as conn:
            await conn.execute(CREATE_TABLE)
        logger.info("Database initialized — chat_history table ready.")

    async def add_message(self, user_id: str, role: str, content: str):
        """Insert a message into chat history."""
        async with self.pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO chat_history (user_id, role, content) VALUES ($1, $2, $3)",
                user_id,
                role,
                content,
            )

    async def get_history(self, user_id: str, limit: int = 5) -> list[dict]:
        """
        Fetch the last `limit` message *pairs* (user + assistant) for a user.

        Returns them in chronological order as:
            [{"role": "user", "content": "..."}, {"role": "assistant", "content": "..."}, ...]
        """
        # Fetch limit*2 rows to get `limit` exchanges (each exchange = user + assistant)
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT role, content
                FROM chat_history
                WHERE user_id = $1
                ORDER BY created_at DESC
                LIMIT $2
                """,
                user_id,
                limit * 2,
            )
        # Reverse to chronological order
        return [{"role": r["role"], "content": r["content"]} for r in reversed(rows)]

    async def clear_history(self, user_id: str):
        """Delete all chat history for a user."""
        async with self.pool.acquire() as conn:
            await conn.execute(
                "DELETE FROM chat_history WHERE user_id = $1",
                user_id,
            )
        logger.info("Cleared chat history for user %s", user_id)

    async def close(self):
        """Close the connection pool."""
        if self.pool:
            await self.pool.close()
