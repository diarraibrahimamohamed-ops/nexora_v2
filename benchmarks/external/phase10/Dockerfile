FROM python:3.12-slim
ARG VINA_VERSION=1.2.5
ARG FPOCKET_VERSION=4.2.3
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates curl git gcc g++ make libnetcdf-dev \
    && rm -rf /var/lib/apt/lists/*
RUN curl -fsSL "https://github.com/ccsb-scripps/AutoDock-Vina/releases/download/v${VINA_VERSION}/vina_${VINA_VERSION}_linux_x86_64" \
    -o /usr/local/bin/vina && chmod 0755 /usr/local/bin/vina
RUN git clone --depth 1 --branch "${FPOCKET_VERSION}" https://github.com/Discngine/fpocket.git /tmp/fpocket \
    && make -C /tmp/fpocket \
    && cp /tmp/fpocket/bin/fpocket /usr/local/bin/fpocket \
    && chmod 0755 /usr/local/bin/fpocket \
    && rm -rf /tmp/fpocket
COPY requirements-benchmark.txt /tmp/requirements-benchmark.txt
RUN pip install --no-cache-dir -r /tmp/requirements-benchmark.txt
WORKDIR /workspace
ENTRYPOINT ["python"]
