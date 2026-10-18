# Seed for a `fit: runtime` Java lab. Add a builder stage (builder/java) or
# copy a prebuilt artifact, then ship your own /start.sh. Runtime labs have no
# fixed listen port, so declare one in challenge.yaml.
ARG BASE_JAVA_IMAGE=base/java:latest
FROM ${BASE_JAVA_IMAGE}

COPY start.sh /start.sh
COPY app.jar /app/app.jar
