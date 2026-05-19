FROM nvidia/cuda:12.4.1-cudnn-devel-ubuntu22.04

ENV DEBIAN_FRONTEND=noninteractive
ENV APP_ENV=avantev02
ENV EE_PROJECT=ee-mapa01
ENV APP_GEO_PATH=Data/VisitaGFP.shp
ENV APP_EXPORT_DIR=export
ENV S2DR4_WHEEL_URL=https://storage.googleapis.com/0x7ff601307fa5/s2dr4-20260518.1-cp312-cp312-linux_x86_64.whl
ENV S2DR4_WHEEL_PATH=/opt/wheels/s2dr4-20260518.1-cp312-cp312-linux_x86_64.whl
ENV PATH=/opt/venv/bin:$PATH

WORKDIR /app

RUN apt-get update \
  && apt-get install -y --no-install-recommends \
    software-properties-common \
    curl \
    ca-certificates \
    git \
    build-essential \
    gdal-bin \
    libgdal-dev \
    libspatialindex-dev \
  && add-apt-repository ppa:deadsnakes/ppa -y \
  && apt-get update \
  && apt-get install -y --no-install-recommends \
    python3.12 \
    python3.12-dev \
    python3.12-venv \
  && rm -rf /var/lib/apt/lists/*

COPY requirements.txt requirements-coderoom.txt ./

RUN python3.12 -m venv /opt/venv \
  && python -m pip install --upgrade pip setuptools wheel \
  && pip install --index-url https://download.pytorch.org/whl/cu124 torch torchvision torchaudio \
  && pip install -r requirements-coderoom.txt \
  && mkdir -p /opt/wheels \
  && curl -L "$S2DR4_WHEEL_URL" -o "$S2DR4_WHEEL_PATH" \
  && pip install "$S2DR4_WHEEL_PATH"

COPY . .

RUN mkdir -p /app/export /app/auth /content/output

EXPOSE 8787

CMD ["python", "app.py", "--host", "0.0.0.0", "--port", "8787"]

