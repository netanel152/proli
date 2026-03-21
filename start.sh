#!/bin/bash
set -e

echo "Starting Proli services..."

# Start Worker in background
python -m app.worker &
WORKER_PID=$!
echo "Worker started (PID: $WORKER_PID)"

# Start Admin Panel in background
streamlit run admin_panel/main.py --server.port 8501 --server.address 0.0.0.0 &
ADMIN_PID=$!
echo "Admin panel started (PID: $ADMIN_PID)"

# Trap to clean up background processes on exit
cleanup() {
    echo "Shutting down..."
    kill $WORKER_PID $ADMIN_PID 2>/dev/null || true
    wait $WORKER_PID $ADMIN_PID 2>/dev/null || true
    echo "All processes stopped."
}
trap cleanup EXIT SIGTERM SIGINT

# Start API in foreground (keeps container alive)
echo "Starting API server..."
uvicorn app.main:app --host 0.0.0.0 --port 8000
