FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Default: offline stub, no key needed. Override the command to use Groq:
#   docker run --env-file .env -v "$PWD/outputs:/app/outputs" migration-tool --llm groq --reset-memory
ENTRYPOINT ["python", "main.py"]
CMD ["--llm", "fake", "--reset-memory"]
