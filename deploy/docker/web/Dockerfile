# Web image: static SPA build served by non-root nginx. Build context is frontend/; the nginx files come
# from the named context `webconf` (deploy/docker/web), see compose.web.yaml.
FROM node:24-alpine@sha256:8a32c8cf649a49915a66e81e7a609223c708d0adabf288ee3f4077d765542f85 AS build
WORKDIR /src
COPY package.json package-lock.json ./
RUN npm ci --ignore-scripts
COPY . .
# Production mode: no MSW, no mock data, and no API credential (the JWT is injected by nginx at runtime).
RUN npx vite build

FROM nginxinc/nginx-unprivileged:1.29-alpine@sha256:0c79d56aee561a1d81c63f00eee5fb5fe29279560cdc55e91425133104c7fbe6
COPY --from=build /src/dist /usr/share/nginx/html
COPY --from=webconf nginx.conf /etc/nginx/nginx.conf
COPY --from=webconf entrypoint.sh /usr/local/bin/web-entrypoint.sh
# nginx-unprivileged already runs as uid 101; keep it explicit.
USER 101:101
EXPOSE 8080
ENTRYPOINT ["/usr/local/bin/web-entrypoint.sh"]
CMD ["nginx", "-g", "daemon off;"]
