FROM nvidia/cuda:12.6.3-cudnn-devel-ubuntu24.04

ENV DEBIAN_FRONTEND=noninteractive
ENV APP_ENV=avantev02
ENV EE_PROJECT=ee-mapa01
ENV APP_GEO_PATH=Data/VisitaGFP.shp
ENV APP_EXPORT_DIR=export
ENV S2DR4_WHEEL_URL=https://storage.googleapis.com/0x7ff601307fa5/s2dr4-20260518.1-cp312-cp312-linux_x86_64.whl
ENV S2DR4_WHEEL_PATH=/opt/wheels/s2dr4-20260518.1-cp312-cp312-linux_x86_64.whl
ENV S2DR4_VENDOR_WHEEL=/app/vendor/wheels/s2dr4-20260518.1-cp312-cp312-linux_x86_64.whl
ENV S2DR4_MODEL_URL=https://storage.googleapis.com/0x7ff601307fa3/S2DR4-GL-20241022.1
ENV S2DR4_MODEL_DIR=/var/local/S2DR3
ENV S2DR4_MODEL_PATH=/var/local/S2DR3/S2DR4-GL-20241022.1
ENV S2DR4_MODEL=/var/local/S2DR3/S2DR4-GL-20241022.1
ENV SYSTEM_MODEL=/var/local/S2DR3/S2DR4-GL-20241022.1
ENV S2DR4_MODEL_BYTES=840950890
ENV S2DR4_FORCE_CPU=1
ENV S2DR4_IMPORT_TIMEOUT_SECONDS=300
ENV S2DR4_TORCH_THREADS=1
ENV S2DR4_COLAB_COMPAT=1
ENV COLAB_GPU=0
ENV CUDA_VISIBLE_DEVICES=
ENV NVIDIA_VISIBLE_DEVICES=none
ENV MPLBACKEND=Agg
ENV OMP_NUM_THREADS=1
ENV MKL_NUM_THREADS=1
ENV OPENBLAS_NUM_THREADS=1
ENV NUMEXPR_MAX_THREADS=1
ENV PYTHONUNBUFFERED=1
ENV PATH=/opt/venv/bin:$PATH

ARG TORCH_INDEX_URL=https://download.pytorch.org/whl/cpu

WORKDIR /app

RUN apt-get update \
  && apt-get install -y --no-install-recommends \
    curl \
    ca-certificates \
    git \
    build-essential \
    gdal-bin \
    libgdal-dev \
    python3-gdal \
    libspatialindex-dev \
    python3.12 \
    python3.12-dev \
    python3.12-venv \
  && rm -rf /var/lib/apt/lists/*

COPY requirements.txt requirements-coderoom.txt ./
COPY vendor/wheels/ ./vendor/wheels/

RUN python3.12 -m venv --system-site-packages /opt/venv \
  && python -m pip install --upgrade pip setuptools wheel \
  && pip install --index-url "$TORCH_INDEX_URL" torch torchvision torchaudio \
  && pip install -r requirements-coderoom.txt \
  && pip install --no-deps py_tools_ds==0.24.1 geoarray==0.19.2 arosics==1.13.2 \
  && mkdir -p /opt/wheels \
  && if [ -f "$S2DR4_VENDOR_WHEEL" ]; then \
       cp "$S2DR4_VENDOR_WHEEL" "$S2DR4_WHEEL_PATH"; \
     else \
       curl --fail -L --retry 5 --retry-delay 5 --retry-connrefused "$S2DR4_WHEEL_URL" -o "$S2DR4_WHEEL_PATH"; \
     fi \
  && pip install --no-deps "$S2DR4_WHEEL_PATH" \
  && mkdir -p "$S2DR4_MODEL_DIR" \
  && curl --fail -L --retry 5 --retry-delay 5 --retry-connrefused "$S2DR4_MODEL_URL" -o "$S2DR4_MODEL_PATH" \
  && test "$(wc -c < "$S2DR4_MODEL_PATH")" = "$S2DR4_MODEL_BYTES"

COPY . .

RUN mkdir -p /app/export /app/auth /content/output /content/datapath /content/logs

RUN python scripts/validate_coderoom.py

EXPOSE 8080

CMD ["python", "-u", "app.py", "--host", "0.0.0.0"]
