FROM python:3.12-slim

WORKDIR /app
COPY pyproject.toml README.md ./
COPY sliderule ./sliderule
COPY alembic.ini ./
COPY alembic ./alembic
RUN pip install --no-cache-dir .

# api by default; the worker service overrides the command
EXPOSE 8000
CMD ["uvicorn", "sliderule.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
