# Imagen del frontend: compila Vue y sirve con Nginx sin privilegios. Contexto: la raíz del
# repositorio. Las dos imágenes base llegan fijadas por digest y sin valor por defecto.
ARG NODE_IMAGE
ARG NGINX_IMAGE

FROM ${NODE_IMAGE} AS build
WORKDIR /app
COPY frontend/package.json frontend/package-lock.json frontend/.npmrc ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM ${NGINX_IMAGE}
COPY --from=build /app/dist /usr/share/nginx/html
COPY deploy/nginx/nginx.conf /etc/nginx/nginx.conf
COPY deploy/nginx/snippets /etc/nginx/snippets

# La imagen base ya es nginx-unprivileged (101); se declara para que quede explícito y verificable.
USER 101:101
