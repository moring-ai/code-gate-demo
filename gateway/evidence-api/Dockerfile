# Stdlib-only service: no pip installs, nothing to pull from PyPI.
FROM python:3.12-slim
RUN useradd --system --uid 10001 evidence && mkdir /data && chown evidence /data
COPY server.py /app/server.py
USER evidence
EXPOSE 8787
CMD ["python", "/app/server.py"]
