FROM python:3.12-slim

# Build deps (cmake/gcc) required for pyopenjtalk C extension compilation.
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    fonts-noto-cjk \
    curl \
    mecab \
    libmecab-dev \
    cmake \
    make \
    gcc \
    g++ \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Download UniDic full dictionary (~900MB) for accent estimation (Issue #31).
# Only runs once per image build — cached unless requirements.txt changes.
RUN python -m unidic download

COPY . .

RUN mkdir -p output data assets/bgm assets/op assets/ed

EXPOSE 8080

CMD ["python", "-m", "app.main"]
