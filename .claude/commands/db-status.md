Show the current state of the Proli database:

1. Connect to MongoDB (use MONGO_URI from .env or default localhost)
2. Count documents in each collection: users, leads, messages, slots, settings, reviews, consent, audit_log, admins
3. Show active professionals count and their names
4. Show leads by status (new, contacted, booked, completed, rejected, closed, cancelled)
5. Show pending approval professionals (if any)
6. Check if indexes exist (run create_indexes.py if missing)

Use `python -c "..."` with pymongo to query the database. Display results in a clear table format.
