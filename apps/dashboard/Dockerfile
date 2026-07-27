# Dashboard — build context is apps/dashboard:
#   docker build -t receptionist-web apps/dashboard
# Next.js standalone output; non-root runtime; no secrets baked in
# (NEXT_PUBLIC_* values are injected at build via --build-arg, server
# secrets at runtime from Secret Manager).

FROM node:22-slim AS deps
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci

FROM node:22-slim AS builder
WORKDIR /app
COPY --from=deps /app/node_modules node_modules
COPY . .
ARG NEXT_PUBLIC_APP_URL
ENV NEXT_PUBLIC_APP_URL=$NEXT_PUBLIC_APP_URL \
    NEXT_TELEMETRY_DISABLED=1
RUN npm run build

FROM node:22-slim
RUN groupadd --system app && useradd --system --gid app --create-home app
WORKDIR /app
ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1
COPY --from=builder --chown=app:app /app/.next/standalone ./
COPY --from=builder --chown=app:app /app/.next/static .next/static
COPY --from=builder --chown=app:app /app/public public
USER app
EXPOSE 8080
ENV PORT=8080 HOSTNAME=0.0.0.0
CMD ["node", "server.js"]
