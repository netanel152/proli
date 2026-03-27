Pre-deployment checklist for Proli. Run through these checks:

1. Run `pytest --tb=short` — verify all tests pass (except 2 known-failing)
2. Check `.env` has all required vars: GREEN_API_INSTANCE_ID, GREEN_API_TOKEN, GEMINI_API_KEY, CLOUDINARY_CLOUD_NAME, CLOUDINARY_API_KEY, CLOUDINARY_API_SECRET
3. Check if WEBHOOK_TOKEN is set (warn if not — production should have it)
4. Check if ENVIRONMENT is set to "production" (warn if "development")
5. Verify `python scripts/create_indexes.py` has been run (check for index creation script)
6. Check Docker build: `docker-compose build --no-cache`
7. Report a pass/fail checklist summary.
