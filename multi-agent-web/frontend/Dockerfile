# Frontend deployment image: build React once, then serve it with Nginx.
FROM node:22-bookworm-slim AS build

WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . ./
RUN npm run build

FROM nginx:1.27-alpine

COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/templates/default.conf.template

# Cloud Run injects this only at runtime. Do not put the backend URL in Vite.
ENV BACKEND_ORIGIN=""

EXPOSE 8080
CMD ["nginx", "-g", "daemon off;"]
