Show recent Proli logs, filtered by importance.

If running in Docker:
- `docker-compose logs --tail=100 worker` for worker logs
- `docker-compose logs --tail=100 api` for API logs

If running locally:
- Read the latest `logs/proli.log` file

Filter for errors and warnings. Highlight:
- Failed WhatsApp sends
- AI/Gemini failures
- MongoDB connection issues
- Redis connection issues
- SOS alerts
- Rate limit hits

Summarize what's healthy and what needs attention.
