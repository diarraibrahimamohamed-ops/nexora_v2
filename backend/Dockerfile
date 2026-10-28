FROM python:3.12-slim
ARG INSTALL_COLABFOLD=false
ARG INSTALL_AMRFINDERPLUS=false
ARG AMRFINDERPLUS_VERSION=4.2.7
RUN apt-get update && apt-get install -y --no-install-recommends \
        wget curl build-essential libboost-all-dev \
    && rm -rf /var/lib/apt/lists/*
RUN wget -q https://github.com/ccsb-scripps/AutoDock-Vina/releases/download/v1.2.5/vina_1.2.5_linux_x86_64 \
        -O /usr/local/bin/vina && chmod +x /usr/local/bin/vina
WORKDIR /app
ENV PYTHONPATH=/app
COPY requirements.txt .
RUN pip install --no-cache-dir --timeout 300 -r requirements.txt
RUN if [ "$INSTALL_COLABFOLD" = "true" ]; then \
                pip install --no-cache-dir --timeout 300 -r requirements-structure.txt; \
        fi

# Optional AMRFinderPlus installation. The biological reference database is
# intentionally updated separately and persisted under /storage/reference.
# Build with --build-arg INSTALL_AMRFINDERPLUS=true to install the pinned binary.
RUN if [ "$INSTALL_AMRFINDERPLUS" = "true" ]; then \
        apt-get update && apt-get install -y --no-install-recommends \
            ca-certificates curl hmmer ncbi-blast+ libcurl4 libgomp1 perl \
        && mkdir -p /opt/amrfinder \
        && curl -fsSL "https://github.com/ncbi/amr/releases/download/amrfinder_v${AMRFINDERPLUS_VERSION}/amrfinder_binaries_v${AMRFINDERPLUS_VERSION}.tar.gz" -o /tmp/amrfinder.tar.gz \
        && tar -xzf /tmp/amrfinder.tar.gz -C /opt/amrfinder \
        && find /opt/amrfinder -type f -name amrfinder -exec ln -sf {} /usr/local/bin/amrfinder \; \
        && find /opt/amrfinder -type f -name amrfinder_update -exec ln -sf {} /usr/local/bin/amrfinder_update \; \
        && test -x /usr/local/bin/amrfinder \
        && rm -f /tmp/amrfinder.tar.gz \
        && rm -rf /var/lib/apt/lists/*; \
    fi
COPY . .
RUN chmod +x /app/entrypoint.sh
ENTRYPOINT ["/app/entrypoint.sh"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
