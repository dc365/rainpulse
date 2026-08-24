FROM python:3.13.12-slim-bookworm

ENV HOME=/tmp \
    PYTHONPATH=/opt/rainpulse/site-packages:/opt/rainpulse \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

COPY .build/worker-site-packages /opt/rainpulse/site-packages
COPY algorithms/rainpulse_algo /opt/rainpulse/rainpulse_algo

USER 65532:65532
ENTRYPOINT ["python", "-m", "rainpulse_algo.worker"]
