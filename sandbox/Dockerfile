FROM python:3.14.8-alpine3.24@sha256:f6a589d43c42b9e7f7dc67a12d37132491f362859a5d750607710cc56da3bc72
RUN apk upgrade --no-cache \
    && apk add --no-cache gcc musl-dev binutils procps curl strace setpriv \
    && addgroup -g 10000 worker \
    && adduser -D -u 10000 -G worker worker
RUN python -m pip uninstall -y pip \
    && python -c "import pathlib, shutil, sysconfig; shutil.rmtree(pathlib.Path(sysconfig.get_path('stdlib')) / 'ensurepip')"
COPY supervisor.py /opt/supervisor.py
RUN chmod 500 /opt/supervisor.py
WORKDIR /workspace
CMD ["python", "/opt/supervisor.py"]
