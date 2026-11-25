# Web frontend image. Build context: the solution folder (the app imports contracts/state-catalog.json):
#   docker build -f web/Dockerfile -t quote-web .
# The BFF URL is fixed at build time (VITE_BFF_URL). Leave it empty: the app then calls /api on its own
# origin, which is how it runs behind Front Door.

FROM node:22-alpine AS build
WORKDIR /src/web
COPY web/package.json web/package-lock.json ./
RUN npm ci --ignore-scripts --no-audit --no-fund
COPY contracts /src/contracts
COPY web/ ./
ARG VITE_BFF_URL=""
ARG VITE_ENABLE_TRACE=false
ENV VITE_BFF_URL=${VITE_BFF_URL} VITE_ENABLE_TRACE=${VITE_ENABLE_TRACE}
RUN npm run build

FROM nginxinc/nginx-unprivileged:1.27-alpine
COPY web/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /src/web/dist /usr/share/nginx/html
USER 101
EXPOSE 8080
HEALTHCHECK CMD wget -q -O /dev/null http://localhost:8080/ || exit 1
