# Stage 1: front-end build. Compiles Tailwind and copies the pinned JS/CSS libraries into static/
# (frontend/assets.json), so production pages don't depend on CDNs or the in-browser Tailwind compiler.
FROM node:20-alpine AS assets
WORKDIR /src/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
COPY static/ /src/static/
COPY templates/ /src/templates/
COPY apps/ /src/apps/
RUN npm run build

# Stage 2: the app (used by migrate, web, worker and beat).
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .
COPY --from=assets /src/static/vendor/ /app/static/vendor/
COPY --from=assets /src/static/css/app.css /app/static/css/app.css
# Static files are collected at build time (WhiteNoise serves them with long-lived, hashed names).
RUN SECRET_KEY=build-only DJANGO_SETTINGS_MODULE=config.settings.base python manage.py collectstatic --noinput

RUN useradd --create-home app && mkdir -p /app/media && chown -R app /app
USER app

EXPOSE 8000
# The web health check lives in docker-compose.prod.yml (worker and beat share this image but serve no HTTP).
CMD ["gunicorn", "config.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "3", "--timeout", "120", "--access-logfile", "-"]
