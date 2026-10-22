# The example shop's API: opens deposits on HDPay with the project's secret key, follows its events
# and emails the customer. Its SQLite database lives in /data, a volume.
#
#   docker build -t hdpay-example-api example/api
#   docker run -p 8400:8400 -v shop-data:/data -e HDPAY_URL=http://hdpay-api:8090 -e HDPAY_SECRET_KEY=hdp_sk_... hdpay-example-api
#   docker run --rm -v shop-data:/data hdpay-example-api python -m source.users seed

FROM python:3.14-slim
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HOST=0.0.0.0 \
    PORT=8400 \
    RELOAD=false \
    DATABASE=/data/example.db
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
 && useradd --create-home --uid 10001 shop \
 && mkdir /data && chown shop /data
COPY source ./source
USER shop
VOLUME /data
EXPOSE 8400
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8400/health', timeout=3)"
CMD ["python", "-m", "source"]
