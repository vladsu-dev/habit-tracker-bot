FROM python:3.13-slim
WORKDIR /service
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 POETRY_VIRTUALENVS_CREATE=false
RUN pip install --no-cache-dir poetry==2.5.1
COPY pyproject.toml poetry.lock ./
RUN poetry install --only main --no-root --no-interaction
COPY app app
COPY migrations migrations
COPY alembic.ini .
RUN useradd --create-home tracker
USER tracker
