"""PRO-253: the Mongo fallback of get_chat_history returns the NEWEST messages.

It sorted ascending and limited, so a conversation longer than the window gave
the model its opening turns and dropped the latest ones — the Redis path takes
``[-limit:]``, so the two disagreed exactly when the cache was cold.
"""

from datetime import datetime, timedelta, timezone

import pytest

import app.services.workflow_service as workflow_service
from app.db.indexes import INDEX_SPECS


@pytest.mark.asyncio
async def test_a_long_conversation_falls_back_to_its_newest_messages_in_order(mock_db):
    chat_id = "972501234999@c.us"
    await mock_db.messages.delete_many({"chat_id": chat_id})
    limit = 10
    start = datetime(2026, 11, 1, 8, 0, tzinfo=timezone.utc)
    await mock_db.messages.insert_many(
        [
            {
                "chat_id": chat_id,
                "role": "user" if n % 2 == 0 else "model",
                "text": f"m{n}",
                "timestamp": start + timedelta(minutes=n),
            }
            for n in range(limit + 5)
        ]
    )

    history = await workflow_service.lead_manager.get_chat_history(chat_id, limit=limit)

    assert [m["parts"][0] for m in history] == [f"m{n}" for n in range(5, limit + 5)]


def test_messages_are_indexed_for_the_fallbacks_query():
    keys = [list(keys) for label, _c, keys, _k in INDEX_SPECS if label == "Messages"]
    assert [("chat_id", 1), ("timestamp", 1)] in keys
