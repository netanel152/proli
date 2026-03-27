Help the user add a new professional to the database.

Ask for (or parse from $ARGUMENTS):
- Business name (Hebrew)
- Phone number (972 format)
- Type: plumber/electrician/handyman/locksmith/painter/cleaner/general
- Service areas (comma-separated cities)
- Prices (optional)

Then generate a MongoDB insert command using `python -c "..."` that:
1. Connects to the configured MONGO_URI
2. Inserts the professional document with all required fields (role, is_active, social_proof, plan, location from ISRAEL_CITIES_COORDS if available)
3. Confirms the insert was successful

Alternatively, remind the user they can also add pros via the admin panel at http://localhost:8501 or via WhatsApp onboarding (send "הרשמה").
