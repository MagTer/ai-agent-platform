"""SQLite backed metadata store."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from pathlib import Path

from .models import AgentMessage


class StateStore:
    """Persist lightweight chat metadata for observability."""

    def __init__(self, database_path: Path) -> None:
        self._database_path = database_path
        self._initialise()

    def _initialise(self) -> None:
        self._database_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self._database_path) as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS interactions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    conversation_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            connection.commit()

    def append_messages(
        self, conversation_id: str, messages: Iterable[AgentMessage]
    ) -> None:
        """Persist chat messages related to a conversation."""

        with sqlite3.connect(self._database_path) as connection:
            connection.executemany(
                """
                INSERT INTO interactions (conversation_id, role, content)
                VALUES (?, ?, ?)
                """,
                (
                    (conversation_id, message.role, message.content)
                    for message in messages
                ),
            )
            connection.commit()

    def get_messages(self, conversation_id: str, limit: int = 20) -> list[AgentMessage]:
        """Return the latest messages for a conversation."""

        with sqlite3.connect(self._database_path) as connection:
            cursor = connection.execute(
                """
                SELECT role, content
                FROM interactions
                WHERE conversation_id = ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (conversation_id, limit),
            )
            rows = cursor.fetchall()

        messages = [AgentMessage(role=row[0], content=row[1]) for row in reversed(rows)]
        return messages


__all__ = ["StateStore"]
