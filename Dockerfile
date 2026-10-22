# Build: docker build -t stratonext-port .
# Run:   docker run --rm --env-file .env -p 8000:8000 stratonext-port
FROM python:3.14-slim

RUN pip install --no-cache-dir 'poetry>=2.0.0,<3.0.0'

WORKDIR /app

# Deps first so a code edit doesn't re-resolve the lock. poetry.toml puts the venv in-project.
COPY pyproject.toml poetry.lock poetry.toml ./
RUN poetry install --without dev --no-root --no-interaction --no-ansi --no-cache

COPY . .

# run_server.py serves the /integration/tasks/* webhook endpoints here.
EXPOSE 8000

# ponytail: the venv's python directly: no Poetry at runtime, no network on start.
CMD ["/app/.venv/bin/python", "run_server.py"]
