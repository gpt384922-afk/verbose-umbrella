FROM python:3.11-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        chromium \
        chromium-driver \
        fonts-liberation \
        libnss3 \
        libxss1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY ycbot/ ./ycbot/
COPY .env.example ./.env.example

RUN mkdir -p /data/chrome-profile

ENV YC_CENTER_CHROME_BINARY=/usr/bin/chromium \
    YC_CENTER_CHROME_USER_DATA_DIR=/data/chrome-profile \
    YC_CENTER_SELENIUM_HEADLESS=true \
    YC_CENTER_SELENIUM_QUIT=true

CMD ["python", "-m", "ycbot.bot"]
