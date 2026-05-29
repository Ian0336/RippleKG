FROM python:3.12-slim

WORKDIR /app

# Install dependencies first (cached layer). Editable install so that the
# mounted ./src in docker-compose gives hot-reload without a rebuild.
COPY pyproject.toml ./
COPY src ./src
RUN pip install --no-cache-dir -e .

COPY scripts ./scripts

EXPOSE 8000
CMD ["uvicorn", "ripplekg.service.api:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]
