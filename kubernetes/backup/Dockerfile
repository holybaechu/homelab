FROM restic/restic:0.19.1 AS restic
FROM registry.k8s.io/kubectl:v1.37.2 AS kubectl
FROM postgres:18.6-trixie
RUN apt-get update \
    && apt-get install -y --no-install-recommends python3 rsync git postgresql-client-14 ca-certificates \
    && rm -rf /var/lib/apt/lists/*
COPY --from=restic /usr/bin/restic /usr/local/bin/restic
COPY --from=kubectl /bin/kubectl /usr/local/bin/kubectl
ENV PYTHONDONTWRITEBYTECODE=1
ENTRYPOINT ["python3"]
