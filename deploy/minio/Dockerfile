FROM scratch

COPY .build/linux-amd64/minio /minio
COPY .build/linux-amd64/rainpulse-healthcheck /rainpulse-healthcheck
COPY --chown=65532:65532 deploy/minio/data /data

ENV HOME=/data
USER 65532:65532
EXPOSE 9000 9001
ENTRYPOINT ["/minio"]
CMD ["server", "/data", "--address", ":9000", "--console-address", ":9001"]
