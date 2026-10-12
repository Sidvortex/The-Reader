# The Reader as a website: visitors read public GitHub repositories and single Python files.
#
#   docker build -t the-reader .
#   docker run --rm -p 8765:8765 the-reader        then open http://localhost:8765
#
# Hosts like Render and Railway build this file themselves and pass the port to use in $PORT.
FROM python:3.13-slim

# git downloads the repositories people paste
RUN apt-get update \
 && apt-get install -y --no-install-recommends git \
 && rm -rf /var/lib/apt/lists/*

# run as an ordinary user, not root
RUN useradd --create-home --shell /usr/sbin/nologin reader

WORKDIR /app
COPY pyproject.toml README.md LICENSE.md THIRD_PARTY_NOTICES.md ./
COPY src ./src
RUN pip install --no-cache-dir . && rm -rf /app/src

USER reader
ENV THE_READER_PUBLIC=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8765
EXPOSE 8765

CMD ["sh", "-c", "exec the-reader --host 0.0.0.0 --port \"$PORT\" --no-browser"]
