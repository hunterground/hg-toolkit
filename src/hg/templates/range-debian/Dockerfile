ARG RANGE_DEBIAN_IMAGE=range/debian:latest
FROM ${RANGE_DEBIAN_IMAGE}

ARG RANGE_PACK_SSHD_IMAGE=range/pack/sshd:latest

RUN apt-get update \
 && apt-get install -y --no-install-recommends openssh-server \
 && rm -rf /var/lib/apt/lists/*
COPY --from=${RANGE_PACK_SSHD_IMAGE} /hg-pack/root/ /
COPY --from=${RANGE_PACK_SSHD_IMAGE} /hg-pack/manifest.json /usr/lib/hg/packs/sshd/manifest.json
RUN hg-enable sshd && hg-seal
