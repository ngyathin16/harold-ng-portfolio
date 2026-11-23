FROM python:3.12-slim-bookworm@sha256:935c3c5b12357a7e10caa9c1e6ca5692344d97cf229832ef584be2cec9fc03df AS app
ARG APP_COMMIT=development
ARG APP_SOURCE_STATE=unverified
LABEL org.opencontainers.image.revision=$APP_COMMIT org.research.source-state=$APP_SOURCE_STATE org.research.download-contract=disabled-v1
WORKDIR /app
COPY service/requirements.txt /app/service-requirements.txt
RUN pip install --no-cache-dir -r /app/service-requirements.txt
COPY service /app/service
COPY solver /app/solver
COPY data /app/data
COPY migrations /app/migrations
COPY deploy /app/deploy
RUN useradd --uid 10001 --create-home research
USER research
ENV PYTHONUNBUFFERED=1 NUMBA_CACHE_DIR=/tmp/numba-cache
EXPOSE 8080
CMD ["uvicorn", "service.app:app", "--host", "0.0.0.0", "--port", "8080", "--no-access-log"]

FROM postgres:17-bookworm@sha256:837bed18782820fe967067cb5328b02b0d9992c1be9a37ee0c9c492cfc03af3b AS operator
COPY --from=app /usr/local /usr/local
COPY --from=app /app /app
COPY ops /app/ops
RUN apt-get update && apt-get install -y --no-install-recommends libexpat1 libffi8 libbz2-1.0 liblzma5 libsqlite3-0 libgomp1 \
    && rm -rf /var/lib/apt/lists/* \
    && ldconfig && useradd --uid 10001 --create-home research \
    && python -m pip install --no-cache-dir -r /app/ops/backup-requirements.txt \
    && python -c "import ssl, ctypes, lzma, bz2, sqlite3, psycopg, numpy; print('Operator runtime imports passed')"
WORKDIR /app
USER research
ENV PYTHONUNBUFFERED=1
ENTRYPOINT ["python", "-m", "deploy.runtime"]

FROM app AS service
