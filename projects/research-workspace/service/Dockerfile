FROM python:3.12-slim
WORKDIR /app
COPY service/requirements.txt /app/service-requirements.txt
RUN pip install --no-cache-dir -r /app/service-requirements.txt
COPY service /app/service
COPY solver /app/solver
COPY data /app/data
RUN useradd --create-home research
USER research
ENV PYTHONUNBUFFERED=1 NUMBA_CACHE_DIR=/tmp/numba-cache
EXPOSE 8080
CMD ["uvicorn", "service.app:app", "--host", "0.0.0.0", "--port", "8080", "--no-access-log"]
