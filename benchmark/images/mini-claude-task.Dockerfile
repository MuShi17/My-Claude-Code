# Build a task-specific image without replacing the task's original image.
# Example:
#   docker build --build-arg BASE_IMAGE=alexgshaw/write-compressor:20251031 \
#     -t mini-claude-tbench:write-compressor \
#     -f benchmark/images/mini-claude-task.Dockerfile .
ARG BASE_IMAGE
FROM ${BASE_IMAGE}

ENV MINI_CLAUDE_ROOT=/tmp/mini-claude-py
ENV VIRTUAL_ENV=/tmp/mini-claude-py/.venv
ENV PATH=/tmp/mini-claude-py/.venv/bin:$PATH

COPY src/ /tmp/mini-claude-py/

RUN set -eux; \
    if ! (command -v python3 >/dev/null 2>&1 && \
           python3 -m venv --help >/dev/null 2>&1); then \
        apt-get update; \
        DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
            python3 python3-venv; \
        rm -rf /var/lib/apt/lists/*; \
    fi; \
    python3 -m venv "$VIRTUAL_ENV"; \
    "$VIRTUAL_ENV/bin/pip" install \
        --disable-pip-version-check \
        --no-cache-dir \
        -e "$MINI_CLAUDE_ROOT"; \
    "$VIRTUAL_ENV/bin/python" -c \
        "import anthropic, openai, dotenv, rich, mini_claude"
