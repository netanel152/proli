Simulate an SLA deflection (15-minute silence) for a customer.

Usage:
`python scripts/simulate_sla_deflection.py <phone_number_or_chat_id>`

This command will:
1. Update/create a lead in MongoDB with a `paused_at` timestamp 16 minutes in the past.
2. Set the Redis state to `PAUSED_FOR_HUMAN`.
3. Wait for the background SLA Monitor (running in the worker) to trigger the deflection message on its next pass (every 5 minutes).
