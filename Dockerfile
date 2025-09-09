# syntax=docker/dockerfile:1
FROM python:3.11-slim

# System basics
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install deps
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy app
COPY ReleaseDataGenv2.py .

# Streamlit runtime env
ENV STREAMLIT_SERVER_HEADLESS=true \
    STREAMLIT_SERVER_ADDRESS=0.0.0.0

# Cloud Run will inject $PORT; Streamlit must bind to it
EXPOSE 8080
CMD ["bash", "-lc", "streamlit run ReleaseDataGenv2.py --server.port $PORT --server.address 0.0.0.0"]