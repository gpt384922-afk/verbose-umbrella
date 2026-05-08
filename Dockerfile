FROM python:3.11-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY ycbot/ ./ycbot/
COPY .env.example ./.env.example

CMD ["python", "-m", "ycbot.bot"]
