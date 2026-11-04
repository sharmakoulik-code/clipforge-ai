FROM python:3.11-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --system --create-home --uid 10001 app
WORKDIR /app
COPY backend/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
COPY --chown=app:app backend/ ./
RUN mkdir -p /app/data/uploads /app/data/models && chown -R app:app /app/data
USER app
EXPOSE 8000
CMD ["sh", "-c", "alembic -c alembic.ini upgrade head && exec uvicorn app.main:app --host 0.0.0.0 --port \"${PORT:-8000}\""]
