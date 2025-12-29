#!/bin/bash
set -e

# Function to handle shutdown signals
cleanup() {
    echo "Terminating processes..."
    kill $(jobs -p)
}

# Trap SIGTERM and SIGINT
trap cleanup SIGTERM SIGINT

echo "Starting Fixi Modular Monolith..."

# Start FastAPI in the background
echo "Starting FastAPI on port 8000..."
uvicorn app.main:app --host 0.0.0.0 --port 8000 &
FASTAPI_PID=$!

# Start Streamlit in the background
echo "Starting Streamlit on port 8501..."
streamlit run admin_panel/app.py --server.port 8501 --server.address 0.0.0.0 &
STREAMLIT_PID=$!

# Wait for any process to exit
wait -n

# Exit with status of process that exited first
exit $?
