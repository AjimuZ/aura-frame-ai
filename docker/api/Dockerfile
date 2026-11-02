# api image: the FastAPI service and the Python engine, running as an unprivileged user.
# Stage `core` has no torch stack, the same package set as the engine tests (engine/requirements.txt). Stage
# `full` adds PyTorch with CUDA and transformers (engine/requirements-gpu.txt) and is what compose.yaml builds
# by default.

FROM python:3.12-slim@sha256:aa943ce411423c7582288f18707ff66eddcbaeb510502d747a72aeb34f30d159 AS core

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    HOME=/tmp

RUN apt-get update -qq \
    && apt-get install -y -qq --no-install-recommends libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

# Mount points owned by the service user: a new named volume takes its ownership from these.
RUN groupadd --gid 10001 auraframe \
    && useradd --uid 10001 --gid 10001 --no-create-home --shell /usr/sbin/nologin auraframe \
    && install -d -o 10001 -g 10001 /data /exports /models /photos

COPY engine/requirements.txt /app/requirements.txt
RUN pip install -q -r /app/requirements.txt

COPY engine /app/engine
COPY docker/api/healthcheck.py /app/healthcheck.py

WORKDIR /app/engine
USER 10001:10001
EXPOSE 8000
CMD ["python", "-m", "uvicorn", "--factory", "api.app:create_app_from_env", "--host", "0.0.0.0", "--port", "8000", "--no-access-log", "--no-server-header"]


FROM core AS full

USER root
COPY engine/requirements-gpu.txt /app/requirements-gpu.txt
RUN pip install -q -r /app/requirements-gpu.txt
USER 10001:10001
