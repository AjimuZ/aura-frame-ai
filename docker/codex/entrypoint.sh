#!/bin/sh
# Entrypoint of the codex sidecar: installs the image's fixed config into the Codex home, then runs the command.
#
# The codex-home volume outlives the image, so a config seeded only into an empty volume would keep an older or
# edited copy. Rewriting it on every start makes the image's config the one the CLI reads. If CODEX_HOME is unset or
# the volume cannot be written, the copy fails and the command never runs, so the CLI never starts without it.
# umask 077 keeps files the CLI writes there, such as auth.json, readable by the service user only.
set -eu
umask 077
cp /etc/auraframe/codex/config.toml "$CODEX_HOME/config.toml"
exec "$@"
