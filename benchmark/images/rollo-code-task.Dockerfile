# Build a task-specific image without replacing the task's original image.
# Example:
#   docker build --build-arg BASE_IMAGE=alexgshaw/write-compressor:20251031 \
#     -t rollo-tbench:write-compressor \
#     -f benchmark/images/rollo-code-task.Dockerfile .
ARG BASE_IMAGE
FROM ${BASE_IMAGE}

ENV ROLLO_ROOT=/tmp/rollo
ENV VIRTUAL_ENV=/tmp/rollo/.venv
ENV PATH=/tmp/rollo/.venv/bin:$PATH

COPY src/ /tmp/rollo/

RUN set -eux; \
    if ! (command -v python3 >/dev/null 2>&1 && \
           python3 -m venv --help >/dev/null 2>&1 && \
           python3 -c "import ensurepip" >/dev/null 2>&1); then \
        apt-get update; \
        DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
            python3 python3-venv; \
        rm -rf /var/lib/apt/lists/*; \
    fi; \
    python3 -m venv "$VIRTUAL_ENV"; \
    "$VIRTUAL_ENV/bin/pip" install \
        --disable-pip-version-check \
        --no-cache-dir \
        -e "$ROLLO_ROOT"; \
    "$VIRTUAL_ENV/bin/python" -c \
        "import anthropic, openai, dotenv, rich, rollo"
