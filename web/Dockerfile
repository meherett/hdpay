# The HDPay web app (the admin): built with Node, served by nginx, which also passes /api/ on to the HDPay API.
#
#   docker build -t hdpay-web web
#   docker run -p 4500:80 -e HDPAY_API_URL=http://hdpay-api:8090 hdpay-web

# ---- build
FROM node:24-alpine AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY . .
RUN npx ng build --configuration production

# ---- serve
FROM nginx:1.29-alpine
# links the image to the repository on GitHub (ghcr.io)
LABEL org.opencontainers.image.source="https://github.com/meherett/hdpay"
# where /api/ goes; the default suits docker-compose.yml
ENV HDPAY_API_URL=http://api:8090
COPY nginx.conf.template /etc/nginx/templates/default.conf.template
COPY --from=build /app/dist/web/browser /usr/share/nginx/html
EXPOSE 80
HEALTHCHECK --interval=30s --timeout=5s --retries=3 CMD wget -q --spider http://127.0.0.1/ || exit 1
