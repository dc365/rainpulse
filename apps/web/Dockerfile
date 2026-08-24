FROM scratch

COPY .build/linux-amd64/rainpulse-web /rainpulse-web
COPY apps/web/dist /srv/rainpulse-web

USER 65532:65532
EXPOSE 8080
ENTRYPOINT ["/rainpulse-web"]
