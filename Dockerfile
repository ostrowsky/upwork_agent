# Upwork AI Sales Assistant — container image.
# Base ships Chromium + all system libs Playwright needs, matching playwright==1.60.0.
FROM mcr.microsoft.com/playwright/python:v1.60.0-noble

WORKDIR /app

# Python deps first for layer caching.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Chromium is preinstalled in the base image; ensure it's present for this version.
RUN playwright install chromium

# App code.
COPY . .

# Runtime defaults (overridable via compose / .env). In a container we drive the
# bundled Chromium headless — there is no Microsoft Edge.
ENV UPWORK_BROWSER_CHANNEL=chromium \
    UPWORK_HEADLESS=1 \
    UPWORK_USER_DATA_DIR=/app/data/upwork_profile_chromium \
    PYTHONUNBUFFERED=1

EXPOSE 8501

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD ["python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8501/_stcore/health', timeout=4).status==200 else 1)"]

# Default service is the Streamlit UI; the worker overrides command in compose.
CMD ["python", "-m", "streamlit", "run", "app.py", \
     "--server.port=8501", "--server.address=0.0.0.0", \
     "--server.headless=true", "--browser.gatherUsageStats=false"]
