# The example shop's checkout: built with Node, served by nginx, which also passes /api/ on to the example shop's API.
#
#   docker build -t hdpay-example-web example/web
#   docker run -p 4400:80 -e EXAMPLE_API_URL=http://example-api:8400 hdpay-example-web

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
ENV EXAMPLE_API_URL=http://example-api:8400
COPY nginx.conf.template /etc/nginx/templates/default.conf.template
COPY --from=build /app/dist/web/browser /usr/share/nginx/html
EXPOSE 80
HEALTHCHECK --interval=30s --timeout=5s --retries=3 CMD wget -q --spider http://127.0.0.1/ || exit 1
