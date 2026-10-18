# Seed for a `fit: runtime` Node.js lab. Ship your own /start.sh. Runtime labs
# have no fixed listen port, so declare one in challenge.yaml.
ARG BASE_NODEJS_IMAGE=base/nodejs:latest
FROM ${BASE_NODEJS_IMAGE}

COPY start.sh /start.sh
COPY app.js /app/app.js
COPY package.json /app/package.json
