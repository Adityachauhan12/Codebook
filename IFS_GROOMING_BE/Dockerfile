# --- Stage 1: The Builder ---
FROM python:3.11-bookworm as builder

WORKDIR /opt/venv

RUN python -m venv .
ENV PATH="/opt/venv/bin:$PATH"

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# --- Stage 2: The Final Image ---
FROM python:3.11-slim-bookworm

WORKDIR /app

# Install system dependencies required by OpenCV
RUN apt-get update && apt-get install -y libgl1 && apt-get clean

COPY --from=builder /opt/venv /opt/venv
COPY . .

ENV PATH="/opt/venv/bin:$PATH"

EXPOSE 8000

CMD ["uvicorn", "liveliness_api:app", "--host", "0.0.0.0", "--port", "8000"]