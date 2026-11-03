FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .

# Pass secrets at run time, never bake them into the image:
#   docker run -p 8000:8000 -e APP_PASSWORD=... [-e ANTHROPIC_API_KEY=...] credit-workbench
# The API key is optional: digital PDFs, Excel and CSV are read inside the container.
ENV PORT=8000
EXPOSE 8000
HEALTHCHECK CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:'+__import__('os').environ.get('PORT','8000')+'/api/health')"
CMD uvicorn server:app --host 0.0.0.0 --port ${PORT} --proxy-headers --forwarded-allow-ips "*"
