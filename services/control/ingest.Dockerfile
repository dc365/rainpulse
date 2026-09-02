FROM scratch

COPY .build/linux-amd64/rainpulse-ingest /rainpulse-ingest
COPY .build/linux-amd64/rainpulse-healthcheck /rainpulse-healthcheck
COPY --chown=65532:65532 deploy/ingest-state /var/lib/rainpulse/ingest

USER 65532:65532
EXPOSE 8092
ENTRYPOINT ["/rainpulse-ingest"]
