# Upwork AI Sales Assistant — container image.
# Base ships Chromium + all system libs Playwright needs, matching playwright==1.60.0.
FROM mcr.microsoft.com/playwright/python:v1.60.0-noble

WORKDIR /app

# Xvfb: a virtual X display so we can run Chromium HEADED inside the container.
# Headless Chromium trips Cloudflare on Upwork; a headed browser under Xvfb clears
# it like a real desktop browser.
RUN apt-get update \
    && apt-get install -y --no-install-recommends xvfb \
    && rm -rf /var/lib/apt/lists/*

# Python deps first for layer caching.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Chromium is preinstalled in the base image; ensure it's present for this version.
RUN playwright install chromium

# App code.
COPY . .
RUN chmod +x /app/docker-entrypoint.sh

# Runtime defaults (overridable via compose / .env). We drive the bundled Chromium
# HEADED under Xvfb (see ENTRYPOINT) — there is no Microsoft Edge in the image.
ENV UPWORK_BROWSER_CHANNEL=chromium \
    UPWORK_HEADLESS=0 \
    UPWORK_USER_DATA_DIR=/app/data/upwork_profile_chromium \
    PYTHONUNBUFFERED=1

EXPOSE 8501

# Entrypoint starts Xvfb then execs the service, so any browser launch (worker
# probe/submit, UI buttons) gets a real headed Chromium on a virtual display, and
# the service itself is the main process (crashes are visible / restart works).
ENTRYPOINT ["/app/docker-entrypoint.sh"]

# Default service is the Streamlit UI; the worker overrides command in compose.
CMD ["python", "-m", "streamlit", "run", "app.py", \
     "--server.port=8501", "--server.address=0.0.0.0", \
     "--server.headless=true", "--browser.gatherUsageStats=false"]
