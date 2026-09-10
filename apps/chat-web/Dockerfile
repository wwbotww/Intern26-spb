FROM node:22-alpine AS build

WORKDIR /app

COPY package.json package-lock.json ./
RUN npm ci

COPY . .
ARG VITE_ASSISTANT_UI_MODE=legacy
ARG VITE_AGENT_BROWSER_SESSION=false
RUN VITE_ASSISTANT_UI_MODE=$VITE_ASSISTANT_UI_MODE VITE_AGENT_BROWSER_SESSION=$VITE_AGENT_BROWSER_SESSION npm run build

FROM nginx:1.27-alpine

COPY nginx.conf.template /etc/nginx/templates/default.conf.template
COPY --from=build /app/dist /usr/share/nginx/html

EXPOSE 80
