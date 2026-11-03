# Local/deployment image assembled from the audited, locked linux-x64 modules.
# Context MUST be emitted by stream-cube-context.py; never send the repository root.
FROM --platform=linux/amd64 node:24.22.1-bookworm-slim@sha256:2dd9a4f758e173b65fee15388f7706af6fb01ef50cf7a2c0538711d67a6981ae
ARG CODE_REVISION
LABEL org.opencontainers.image.revision=$CODE_REVISION org.sponsoratlas.project="sponsoratlas" org.sponsoratlas.task="15" org.sponsoratlas.owner="codex-sponsoratlas"
ENV NODE_ENV=production SEMANTIC_SOURCE=parquet SEMANTIC_WORK=/app/work SEMANTIC_SNAPSHOT=/app/snapshot SEMANTIC_CONTAINER=1 SEMANTIC_BIND_HOST=0.0.0.0 PORT=4000
WORKDIR /app/semantic
COPY semantic/ ./
COPY snapshot/ /app/snapshot/
# Source entries and every ancestor remain root-owned; only sibling work is writable.
RUN chmod 444 /app/snapshot/* && chmod 555 /app/snapshot && chmod 755 /app && mkdir /app/work && chown node:node /app/work
USER node
EXPOSE 4000
CMD ["node", "--max-old-space-size=1024", "server.cjs"]
