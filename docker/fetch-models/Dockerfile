# fetch-models image: downloads the pinned model files into the models volume, as an unprivileged user.
# Standard library Python only, so nothing is installed from a package index. Only the fetcher and its
# manifest are copied in; the engine, the photos and the data never are.

FROM python:3.12-slim@sha256:aa943ce411423c7582288f18707ff66eddcbaeb510502d747a72aeb34f30d159

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HOME=/tmp

# The mount point belongs to the service user. Docker copies the ownership of an image directory onto an
# empty named volume when a container mounts it, so the fetcher can write to a models volume that is still
# empty, even when another service created it first as root.
RUN groupadd --gid 10001 auraframe \
    && useradd --uid 10001 --gid 10001 --no-create-home --shell /usr/sbin/nologin auraframe \
    && install -d -o 10001 -g 10001 /models

COPY engine/src/model_fetch /app/model_fetch

WORKDIR /app
USER 10001:10001
ENTRYPOINT ["python", "-m", "model_fetch"]
