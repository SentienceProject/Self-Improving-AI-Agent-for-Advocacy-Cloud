# Analysis image for agent-written code. Built only by sandbox/build.sh.
#
# Pin the base image by digest (see setup notes). build.sh refuses to run
# until this line contains a real @sha256: digest.
FROM python:3.11-slim@sha256:da047cb8f9d1d98e5c070f5300ba9f7274e33b8fc0e5be5ed88740aed1b95ba9

# The container runs read-only as UID 1000 with no home directory, so point
# everything that wants a writable config/cache location at the /tmp tmpfs.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HOME=/tmp \
    MPLCONFIGDIR=/tmp/matplotlib \
    MPLBACKEND=Agg

COPY requirements.txt /tmp/requirements.txt

# --require-hashes: every package must match the hash in requirements.txt.
# --only-binary: install prebuilt wheels only, never run a package's build code.
RUN pip install --no-cache-dir --disable-pip-version-check \
        --require-hashes --only-binary=:all: \
        -r /tmp/requirements.txt \
    && rm /tmp/requirements.txt

WORKDIR /work
