Check the health of the running Proli system:

1. Hit `GET http://localhost:8000/health` and display the response (MongoDB, Redis, Worker status, latencies)
2. Check if Docker containers are running: `docker-compose ps`
3. Check recent worker logs for errors: `docker-compose logs --tail=50 worker`
4. Report any issues found and suggest fixes.
