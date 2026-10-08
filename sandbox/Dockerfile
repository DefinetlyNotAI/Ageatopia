FROM python:3.12-slim-bookworm
RUN apt-get update && apt-get install -y --no-install-recommends gcc libc6-dev binutils procps curl strace util-linux \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --uid 10000 --create-home worker
COPY supervisor.py /opt/supervisor.py
RUN chmod 500 /opt/supervisor.py
WORKDIR /workspace
CMD ["python", "/opt/supervisor.py"]
