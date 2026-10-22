# One image for the three services: api, watcher and sweeper (docker-compose.yml picks the command).

# ---- build: wheels for every dependency. coincurve may have no ready wheel for this Python and
# builds its bundled libsecp256k1 from source, which needs a compiler; the final image does not.
FROM python:3.14-slim AS build
RUN apt-get update \
 && apt-get install -y --no-install-recommends build-essential pkg-config autoconf automake libtool cmake \
 && rm -rf /var/lib/apt/lists/*
WORKDIR /build
COPY requirements.txt .
RUN pip wheel --no-cache-dir --wheel-dir /wheels -r requirements.txt

# ---- run
FROM python:3.14-slim
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HDPAY_RELOAD=false
COPY --from=build /wheels /wheels
RUN pip install --no-cache-dir /wheels/* && rm -rf /wheels \
 && useradd --create-home --uid 10001 hdpay
WORKDIR /app
COPY api ./api
COPY watcher ./watcher
COPY sweeper ./sweeper
COPY chains.example.yaml ./
USER hdpay
EXPOSE 8090
CMD ["python", "-m", "api"]
