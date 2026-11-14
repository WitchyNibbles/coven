# A clean machine for the install smoke job: Debian, git, python3, node, uv and Claude Code, and
# nothing of coven. The repo is mounted at /src when the container runs (see run.sh).
#
# Pins: the Debian image by digest (debian:trixie-slim on 2026-11-13; trixie has python3 3.13), node
# by version and checksum, uv by version and digest, Claude Code by the build argument the workflow
# passes. Bump them on purpose, in one commit.
FROM debian:trixie-slim@sha256:a72b4433de1cf7d6bae0c4cee74d0547f58601a643b3375f070c51600d63b6b2

# No default: a build that forgets the version stops instead of installing the newest one.
ARG COVEN_SMOKE_CLAUDE_VERSION
ARG NODE_VERSION=24.18.0
ARG NODE_SHA256=55aa7153f9d88f28d765fcdad5ae6945b5c0f98a36881703817e4c450fa76742

RUN test -n "$COVEN_SMOKE_CLAUDE_VERSION"

RUN apt-get update \
    && apt-get install --yes --no-install-recommends ca-certificates curl git python3 xz-utils \
    && rm -rf /var/lib/apt/lists/*

# Claude Code needs node 22 or later, which trixie does not package.
RUN curl --fail --silent --show-error --location --output /tmp/node.tar.xz \
        "https://nodejs.org/dist/v${NODE_VERSION}/node-v${NODE_VERSION}-linux-x64.tar.xz" \
    && echo "${NODE_SHA256}  /tmp/node.tar.xz" | sha256sum -c - \
    && tar -xJf /tmp/node.tar.xz -C /usr/local --strip-components=1 \
    && rm /tmp/node.tar.xz

COPY --from=ghcr.io/astral-sh/uv:0.13.8@sha256:5cc1805e2426484abca4ad46ea783bab281fdb225fd82d8ce6d66a81254bac85 /uv /uvx /usr/local/bin/

RUN npm install --global "@anthropic-ai/claude-code@${COVEN_SMOKE_CLAUDE_VERSION}"

# The checkout is owned by the runner's user, not by the user below.
RUN git config --system --add safe.directory '*' \
    && useradd --home-dir /srv/smoke --create-home --uid 1000 smoke

ENV COVEN_SMOKE_CLAUDE_VERSION=${COVEN_SMOKE_CLAUDE_VERSION} \
    DISABLE_AUTOUPDATER=1 \
    HOME=/srv/smoke \
    CLAUDE_CONFIG_DIR=/srv/smoke/claude-config

USER smoke
WORKDIR /srv/smoke

ENTRYPOINT ["sh", "/src/ci/install-smoke/run.sh"]
