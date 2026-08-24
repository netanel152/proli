---
description: Show the current state of the Proli database (collection counts, pros, leads by status, indexes)
---

Show the current state of the Proli database:

1. Connect to MongoDB (use MONGO_URI from .env or default localhost)
2. Count documents in each collection: users, leads, messages, slots, settings, reviews, consent, audit_log, admins, wa_delivery
3. Show active professionals count and their names
4. Show leads by status (new, contacted, booked, completed, rejected, closed, cancelled, pending_admin_review)
5. Show pending approval professionals (if any)
6. Check if indexes exist (run create_indexes.py if missing)

Prefer the MongoDB MCP tools (`mcp__mongodb__*`) when the server is connected; otherwise use the project venv interpreter (`venv/Scripts/python.exe` on Windows, `venv/bin/python` on POSIX — bare `python` may not be on PATH) with pymongo via `-c "..."`. Display results in a clear table format.
