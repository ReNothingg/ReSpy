FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app
COPY pyproject.toml README.md ./
COPY app ./app
RUN pip install --no-cache-dir .

RUN useradd --create-home --uid 10001 respy \
    && mkdir -p /app/data/media \
    && chown -R respy:respy /app/data
USER respy

EXPOSE 8080
CMD ["python", "-m", "app.main"]

