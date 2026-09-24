FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates fonts-liberation libasound2 libatk-bridge2.0-0 libatk1.0-0 \
    libcups2 libdbus-1-3 libdrm2 libgbm1 libgtk-3-0 libnspr4 libnss3 \
    libu2f-udev libvulkan1 libx11-6 libx11-xcb1 libxcb1 libxcomposite1 \
    libxdamage1 libxext6 libxfixes3 libxrandr2 chromium x11-utils xdg-utils xvfb \
    x11vnc xdotool openbox novnc websockify \
    fonts-dejavu-core fonts-noto-core fonts-noto-extra fonts-noto-color-emoji \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY docker/install_xray.py /tmp/install_xray.py
RUN python /tmp/install_xray.py
COPY . .
RUN mkdir -p /app/data /browser-data /app/app/static/fonts \
    && cp /usr/share/fonts/truetype/noto/NotoSansArabic-Regular.ttf /app/app/static/fonts/ \
    && cp /usr/share/fonts/truetype/noto/NotoSans-Regular.ttf /app/app/static/fonts/ \
    && mkdir -p /etc/chromium/policies/managed /etc/opt/chrome/policies/managed \
    && cp /app/docker/chromium-policy.json /etc/chromium/policies/managed/shared-browser.json \
    && cp /app/docker/chromium-policy.json /etc/opt/chrome/policies/managed/shared-browser.json

EXPOSE 8000
ENV DISPLAY=:99
ENV BROWSER_EXECUTABLE_PATH=/usr/bin/chromium
CMD ["sh", "/app/docker-entrypoint.sh"]
