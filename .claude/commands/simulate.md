Simulate a WhatsApp message to the local Proli backend for testing.

Ask the user what message they want to send (or use the argument if provided: $ARGUMENTS).

Then send a properly formatted Green API webhook POST to `http://localhost:8000/webhook` with the message text. Use a test phone number (972501234567). Show the response and then check worker logs for how the message was processed.
