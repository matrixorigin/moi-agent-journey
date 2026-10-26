FROM python:3.12-slim-bookworm
RUN apt-get update && apt-get install -y --no-install-recommends \
    libreoffice-writer libreoffice-calc libreoffice-impress poppler-utils \
    fonts-noto-cjk fonts-liberation && rm -rf /var/lib/apt/lists/*
COPY requirements.txt /requirements.txt
RUN pip install --no-cache-dir -r /requirements.txt
ENV HOME=/tmp PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/app
WORKDIR /app
COPY journey /app/journey
COPY judge /app/judge
USER 1001:1001
ENTRYPOINT ["python", "-m", "judge.verify"]
