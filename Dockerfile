# The three HDPay services, one image each, from a shared base: the same dependencies and code, each
# starting its own service.
#
#   docker build --target api     -t hdpay-api .
#   docker build --target watcher -t hdpay-watcher .
#   docker build --target sweeper -t hdpay-sweeper .
#
#   docker run -p 8090:8090 --env-file envs/common.env hdpay-api
#   docker run --env-file envs/common.env -e HDPAY_CHAIN=sepolia hdpay-watcher
#   docker run --env-file envs/common.env --env-file envs/sweeper.env -e HDPAY_CHAIN=sepolia hdpay-sweeper
#
# Mount chains.yaml at /app/chains.yaml (with HDPAY_CHAINS_FILE=/app/chains.yaml); without it the
# images use chains.example.yaml.

# ---- build: wheels for every dependency. coincurve may have no ready wheel for this Python and
# builds its bundled libsecp256k1 from source, which needs a compiler; the final images do not.
FROM python:3.14-slim AS build
RUN apt-get update  && apt-get install -y --no-install-recommends build-essential pkg-config autoconf automake libtool cmake  && rm -rf /var/lib/apt/lists/*
WORKDIR /build
# coincurve 20's build settings use cmake.verbose, which scikit-build-core 0.10+ refuses: build it with
# an older one (PIP_BUILD_CONSTRAINT for newer pip, PIP_CONSTRAINT for older)
RUN echo "scikit-build-core<0.10" > /build/build-constraints.txt
ENV PIP_BUILD_CONSTRAINT=/build/build-constraints.txt     PIP_CONSTRAINT=/build/build-constraints.txt
COPY requirements.txt .
RUN pip wheel --no-cache-dir --wheel-dir /wheels -r requirements.txt

# ---- base: what the three share
FROM python:3.14-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1     PYTHONUNBUFFERED=1     HDPAY_RELOAD=false
COPY --from=build /wheels /wheels
RUN pip install --no-cache-dir /wheels/* && rm -rf /wheels  && useradd --create-home --uid 10001 hdpay
WORKDIR /app
COPY api ./api
COPY watcher ./watcher
COPY sweeper ./sweeper
COPY chains.example.yaml ./
USER hdpay

# ---- one watcher per chain: HDPAY_CHAIN, or --chain
FROM base AS watcher
CMD ["python", "-m", "watcher"]

# ---- one sweeper per chain, with the master key (HDPAY_MASTER_XPRV): HDPAY_CHAIN, or --chain
FROM base AS sweeper
CMD ["python", "-m", "sweeper"]

# ---- the API (last, so a plain docker build makes it)
FROM base AS api
EXPOSE 8090
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3   CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8090/health', timeout=3)"
CMD ["python", "-m", "api"]
