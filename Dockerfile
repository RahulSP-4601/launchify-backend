FROM python:3.12-bookworm

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PIP_NO_CACHE_DIR=1
ENV NODE_ENV=production

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    curl \
    ffmpeg \
    fonts-liberation \
    libasound2 \
    libatk-bridge2.0-0 \
    libatk1.0-0 \
    libc6 \
    libcairo2 \
    libcups2 \
    libdbus-1-3 \
    libdrm2 \
    libexpat1 \
    libfontconfig1 \
    libgbm1 \
    libgcc-s1 \
    libglib2.0-0 \
    libgtk-3-0 \
    libnspr4 \
    libnss3 \
    libpango-1.0-0 \
    libpangocairo-1.0-0 \
    libstdc++6 \
    libuuid1 \
    libx11-6 \
    libx11-xcb1 \
    libxcb1 \
    libxcomposite1 \
    libxdamage1 \
    libxext6 \
    libxfixes3 \
    libxkbcommon0 \
    libxrandr2 \
    nodejs \
    npm \
    tesseract-ocr \
    xdg-utils \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./requirements.txt

RUN pip install -r requirements.txt

COPY render-worker/package.json render-worker/package-lock.json ./render-worker/

RUN npm ci --prefix ./render-worker

# Warm the Remotion browser into the image so fresh Render instances do not
# spend job time downloading Chrome Headless Shell before each first render.
RUN cd ./render-worker && node --input-type=module -e "const {ensureBrowser}=await import('@remotion/renderer'); await ensureBrowser({logLevel:'info'});"

COPY app ./app
COPY render-worker ./render-worker
COPY start.sh ./start.sh

EXPOSE 8000

CMD ["sh", "./start.sh"]
