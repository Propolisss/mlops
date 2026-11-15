FROM python:3.12.10-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY hw11/requirements.txt .
RUN pip install --no-cache-dir --require-hashes -r requirements.txt \
    && useradd --uid 10001 --create-home app
COPY hw8-9-10/mlops26-hw8-9-10/hw8910-broken/backends.py previous/backends.py
COPY hw8-9-10/mlops26-hw8-9-10/hw8910-broken/release.py previous/release.py
COPY hw8-9-10/mlops26-hw8-9-10/hw8910-broken/service.py previous/service.py
COPY hw11/app.py hw11/load.py ./
USER 10001
EXPOSE 8000
CMD ["uvicorn", "app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--timeout-graceful-shutdown", "45", "--no-access-log"]
