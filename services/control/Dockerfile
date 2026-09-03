FROM scratch

COPY .build/linux-amd64/rainpulse-api /rainpulse-api

WORKDIR /ruiyun-bdp/bdp-dp/bdp-dp-rada/bdp-dp-rada-rainpulse
USER 65532:65532
EXPOSE 8080
ENTRYPOINT ["/rainpulse-api"]
