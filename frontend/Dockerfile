FROM node:22-alpine AS build
WORKDIR /portal
COPY package*.json ./
RUN npm ci --no-audit --no-fund
COPY tsconfig.json vite.config.mjs index.html ./
COPY src/ ./src/
COPY public/ ./public/
RUN npm run build

FROM node:22-alpine AS runtime
WORKDIR /portal
ENV NODE_ENV=production HOST=0.0.0.0 PORT=3000
COPY --from=build --chown=node:node /portal/dist ./dist
COPY --chown=node:node server/index.mjs ./server/index.mjs
USER node
EXPOSE 3000
CMD ["node", "server/index.mjs"]
