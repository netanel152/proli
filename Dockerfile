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

# Copy project files (הכל מועתק כ-root כרגע)
COPY . .

# Copy start script
COPY start.sh .
# נותנים הרשאות ריצה (עדיין כ-root, אז זה יעבוד)
RUN chmod +x start.sh

# Create a non-root user
RUN useradd -m appuser

# Change ownership of the app directory to the new user
# מעבירים את הבעלות על כל הקבצים למשתמש החדש
RUN chown -R appuser:appuser /app

# Switch to non-root user (רק עכשיו עוברים למשתמש המוגבל)
USER appuser

# Expose ports
EXPOSE 8000 8501

# Default command runs the script
CMD ["./start.sh"]