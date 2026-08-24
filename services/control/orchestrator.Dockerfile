FROM scratch

COPY .build/linux-amd64/rainpulse-orchestrator /rainpulse-orchestrator
COPY .build/linux-amd64/rainpulse-healthcheck /rainpulse-healthcheck

USER 65532:65532
EXPOSE 8090
ENTRYPOINT ["/rainpulse-orchestrator"]
CMD ["serve"]
