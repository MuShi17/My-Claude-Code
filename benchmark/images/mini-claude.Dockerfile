FROM python:3.13-slim

ENV MINI_CLAUDE_ROOT=/tmp/mini-claude-py
ENV VIRTUAL_ENV=/tmp/mini-claude-py/.venv
ENV PATH=/tmp/mini-claude-py/.venv/bin:$PATH

# Keep the image independent of the host .env file. API credentials are
# forwarded at runtime by benchmark/harbor_agent.py.
COPY src/ /tmp/mini-claude-py/

RUN python -m venv "$VIRTUAL_ENV" \
    && "$VIRTUAL_ENV/bin/pip" install \
        --disable-pip-version-check \
        --no-cache-dir \
        -e "$MINI_CLAUDE_ROOT" \
    && "$VIRTUAL_ENV/bin/python" -c \
        "import anthropic, openai, dotenv, rich, mini_claude"
