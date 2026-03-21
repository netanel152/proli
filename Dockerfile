# Base Image
FROM python:3.12-slim

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Set working directory
WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Copy project files
COPY . .

# Create a non-root user
RUN useradd -m appuser

# Change ownership of the app directory to the new user
RUN chown -R appuser:appuser /app

# Switch to non-root user
USER appuser

# Expose ports (API: 8000, Admin: 8501)
EXPOSE 8000 8501

# Default: run the API server.
# Override per service:
#   Worker: python -m app.worker
#   Admin:  streamlit run admin_panel/main.py --server.port 8501 --server.address 0.0.0.0
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
