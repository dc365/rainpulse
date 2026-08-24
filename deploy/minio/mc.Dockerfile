FROM scratch

COPY .build/linux-amd64/mc /mc
COPY --chown=65532:65532 deploy/minio/client-data /data

ENV HOME=/data
ENV MC_CONFIG_DIR=/data/.mc
USER 65532:65532
ENTRYPOINT ["/mc"]
