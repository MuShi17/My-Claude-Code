FROM python:3.13-slim

ENV ROLLO_ROOT=/tmp/rollo
ENV VIRTUAL_ENV=/tmp/rollo/.venv
ENV PATH=/tmp/rollo/.venv/bin:$PATH

# Keep the image independent of the host .env file. API credentials are
# forwarded at runtime by benchmark/harbor_agent.py.
COPY src/ /tmp/rollo/

RUN python -m venv "$VIRTUAL_ENV" \
    && "$VIRTUAL_ENV/bin/pip" install \
        --disable-pip-version-check \
        --no-cache-dir \
        -e "$ROLLO_ROOT" \
    && "$VIRTUAL_ENV/bin/python" -c \
        "import anthropic, openai, dotenv, rich, rollo"
