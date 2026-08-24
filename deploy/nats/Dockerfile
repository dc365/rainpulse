FROM scratch

COPY .build/linux-amd64/nats-server /nats-server
COPY .build/linux-amd64/rainpulse-healthcheck /rainpulse-healthcheck
COPY deploy/nats/nats-server.conf /etc/nats/nats-server.conf
COPY --chown=65532:65532 deploy/nats/data /data/jetstream

USER 65532:65532
EXPOSE 4222 8222
ENTRYPOINT ["/nats-server"]
CMD ["-c", "/etc/nats/nats-server.conf"]
