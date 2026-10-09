FROM python:3.15.0rc3-alpine3.24@sha256:f288a00331ddad8ddf86aa9d83331f188d9a5b1999dea3762adf2af1968b0c04 AS busybox-builder
RUN apk add --no-cache build-base linux-headers curl \
    && curl -fsSLo /tmp/busybox.tar.gz https://codeload.github.com/vda-linux/busybox_mirror/tar.gz/e3e1ad1587df43d9eaece0e29702e2089c5cbc10 \
    && printf '%s\n' 'fe995afce62de3e312a8d1960e735a0f64dc46127184d62a6752a4ce76b653fb  /tmp/busybox.tar.gz' | sha256sum -c - \
    && tar -xzf /tmp/busybox.tar.gz -C /tmp
WORKDIR /tmp/busybox_mirror-e3e1ad1587df43d9eaece0e29702e2089c5cbc10
COPY busybox-*.patch /tmp/
RUN patch -p1 < /tmp/busybox-wget.patch \
    && patch -p1 < /tmp/busybox-awk.patch \
    && patch -p1 < /tmp/busybox-ash-recursion.patch \
    && make defconfig \
    && sed -i 's/^CONFIG_TC=y/# CONFIG_TC is not set/' .config \
    && make -j2 \
    && mkdir -p /tmp/package/bin \
    && cp busybox /tmp/package/bin/busybox \
    && ln -s busybox /tmp/package/bin/sh \
    && apk mkpkg --files /tmp/package --output /tmp/busybox.apk \
        --info name:busybox --info version:1.39.0_git-r0 \
        --info description:BusyBox-with-security-backports --info license:GPL-2.0-only \
        --info origin:busybox --info url:https://busybox.net/ \
        --info arch:$(apk --print-arch) --info depends:so:libc.musl-x86_64.so.1 \
        --info provides:/bin/sh --info replaces:busybox-binsh

FROM python:3.15.0rc3-alpine3.24@sha256:f288a00331ddad8ddf86aa9d83331f188d9a5b1999dea3762adf2af1968b0c04
RUN apk upgrade --no-cache \
    && apk add --no-cache clang22 lld22 llvm22 compiler-rt musl-dev procps curl setpriv \
    && printf '%s\n' '--rtlib=compiler-rt' '-fuse-ld=lld' >> /etc/clang22/$(clang -dumpmachine).cfg \
    && ln -s /usr/bin/clang /usr/local/bin/cc \
    && ln -s /usr/bin/clang /usr/local/bin/gcc \
    && ln -s /usr/bin/ld.lld /usr/local/bin/ld \
    && ln -s /usr/lib/llvm22/bin/llvm-objdump /usr/local/bin/objdump \
    && ln -s /usr/lib/llvm22/bin/llvm-strings /usr/local/bin/strings \
    && ln -s /usr/lib/llvm22/bin/llvm-nm /usr/local/bin/nm \
    && ln -s /usr/lib/llvm22/bin/llvm-readelf /usr/local/bin/readelf \
    && addgroup -g 10000 worker \
    && adduser -D -u 10000 -G worker worker
ENV CC=clang
COPY --from=busybox-builder /tmp/busybox.apk /tmp/busybox.apk
RUN apk add --allow-untrusted /tmp/busybox.apk \
    && apk del busybox-binsh ssl_client \
    && busybox --install -s \
    && rm /tmp/busybox.apk
RUN python -m pip uninstall -y pip \
    && python -c "import pathlib, shutil, sysconfig; shutil.rmtree(pathlib.Path(sysconfig.get_path('stdlib')) / 'ensurepip')"
COPY supervisor.py /opt/supervisor.py
COPY process_observer.py /opt/process_observer.py
RUN chmod 500 /opt/supervisor.py /opt/process_observer.py
WORKDIR /workspace
CMD ["python", "/opt/supervisor.py"]
