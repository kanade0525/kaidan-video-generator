FROM python:3.12-slim

# システム依存パッケージのインストール
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    fonts-noto-cjk \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 依存関係のインストール
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# アプリケーションコードのコピー
COPY . .

# 出力ディレクトリの作成
RUN mkdir -p output images

ENTRYPOINT ["python", "main.py"]
