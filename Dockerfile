FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PIP_NO_CACHE_DIR=1 PORT=8000
WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY . .

# Run as an unprivileged user; the app writes nothing to disk.
RUN useradd --create-home --uid 10001 app && chown -R app /app
USER app

# Pass secrets at run time, never bake them into the image:
#   docker run -p 8000:8000 -e APP_PASSWORD=... [-e ANTHROPIC_API_KEY=...] credit-workbench
# The API key is optional: digital PDFs, Excel and CSV are read inside the container.
EXPOSE 8000
HEALTHCHECK CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:'+__import__('os').environ.get('PORT','8000')+'/api/health')"
# --proxy-headers so the real client address (for the sign-in limit) and https (for secure cookies) come through
# Railway's / Render's proxy.
CMD uvicorn server:app --host 0.0.0.0 --port ${PORT} --proxy-headers --forwarded-allow-ips "*" --timeout-keep-alive 30
