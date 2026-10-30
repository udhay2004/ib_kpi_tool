FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .

# Pass secrets at run time, never bake them into the image:
#   docker run -p 8501:8501 -e APP_PASSWORD=... [-e ANTHROPIC_API_KEY=...] credit-workbench
# The API key is optional: digital PDFs, Excel and CSV are read inside the container.
ENV PORT=8501
EXPOSE 8501
HEALTHCHECK CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:'+__import__('os').environ.get('PORT','8501')+'/_stcore/health')"
CMD streamlit run app.py --server.port=${PORT} --server.address=0.0.0.0 --server.headless=true
