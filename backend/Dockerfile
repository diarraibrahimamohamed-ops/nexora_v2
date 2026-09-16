FROM python:3.12-slim
ARG INSTALL_COLABFOLD=false
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
COPY . .
RUN chmod +x /app/entrypoint.sh
ENTRYPOINT ["/app/entrypoint.sh"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
