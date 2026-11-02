# codex image: the pinned Codex CLI for the optional, opt-in judge, run as an unprivileged user. It holds the CLI,
# its fixed config, the entrypoint and the HTTP front that `api` calls; the engine, the photos and the data are never
# copied in.
#
# Pins read from the registries on 2026-11-02: the node:22-slim linux/amd64 manifest digest below, and
# @openai/codex 0.166.0, whose registry integrity npm checks on install:
# sha512-2Szlp1l1cz5TX1ccpX4Zok3DZrot9xeZe0km+6/1v8JmqxADwyJxKBJah4dBymkwtS25j/u6dipJusTOI97Z8Q==

FROM node:22-slim@sha256:9f0dde62d10e7ff7a310ecaebd66225f6b46c17e6cb778449a77b05d3541bea2

ENV NPM_CONFIG_UPDATE_NOTIFIER=false \
    NPM_CONFIG_FUND=false \
    NPM_CONFIG_AUDIT=false \
    HOME=/tmp \
    CODEX_HOME=/codex-home

# The Codex home belongs to the service user, mode 700. Docker copies the owner and mode of an image directory
# onto an empty named volume when a container mounts it, so the codex-home volume starts private to uid 10001.
RUN npm install -g @openai/codex@0.166.0 \
    && npm cache clean --force \
    && groupadd --gid 10001 auraframe \
    && useradd --uid 10001 --gid 10001 --no-create-home --shell /usr/sbin/nologin auraframe \
    && install -d -o 10001 -g 10001 -m 700 /codex-home

COPY config.toml /etc/auraframe/codex/config.toml
COPY entrypoint.sh /usr/local/lib/auraframe/codex-entrypoint.sh
COPY sidecar-server.cjs /usr/local/lib/auraframe/codex-sidecar.cjs

WORKDIR /exchange
USER 10001:10001
ENTRYPOINT ["/bin/sh", "/usr/local/lib/auraframe/codex-entrypoint.sh"]
# The HTTP front that `api` calls on the backend network (ADR 0018). `./start.sh --codex-login` replaces this command
# with `codex login`, so the sign-in runs in the same image, user and Codex home.
CMD ["node", "/usr/local/lib/auraframe/codex-sidecar.cjs"]
