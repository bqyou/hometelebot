FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Create directory for SQLite database (used in dev/single-user mode)
RUN mkdir -p /data

# Environment variable for SQLite path when using local storage
ENV DATABASE_URL=sqlite+aiosqlite:///data/telebot.db

# Run the bot
CMD ["python", "main.py"]
