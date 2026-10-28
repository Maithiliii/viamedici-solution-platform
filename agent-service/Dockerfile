FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY pyproject.toml ./
COPY sep ./sep
RUN pip install ".[dev]"

COPY tests ./tests

EXPOSE 8000
CMD ["uvicorn", "sep.api:app", "--host", "0.0.0.0", "--port", "8000"]
