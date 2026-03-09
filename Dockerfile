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

RUN mkdir -p /data

# Run the bot
CMD ["python", "-c", "import os; vars=[k for k in os.environ if k in ('TELEGRAM_BOT_TOKEN','DATABASE_URL','BOT_MODE')]; print('ENV CHECK:', vars); print('COUNT:', len(os.environ))"]
