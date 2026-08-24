FROM scratch

COPY .build/linux-amd64/rainpulse-api /rainpulse-api

USER 65532:65532
EXPOSE 8080
ENTRYPOINT ["/rainpulse-api"]
