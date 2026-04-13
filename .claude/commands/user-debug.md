Debug a specific user's state and context.

Ask for (or parse from $ARGUMENTS) a phone number or chat_id.

Then run a python command to:
1. Fetch the user's current FSM state from Redis (`state:<chat_id>`).
2. Fetch the user's chat context from Redis (`ctx:<chat_id>`).
3. Fetch the latest lead for this user from MongoDB, showing status and `is_paused` flag.

This is essential for verifying "Zero-Touch" transitions between PRO_MODE and CUSTOMER_MODE.
