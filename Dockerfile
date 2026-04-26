FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml .
COPY sahiixx_bus/ ./sahiixx_bus/
COPY README.md .

RUN pip install --no-cache-dir -e ".[dev]"

EXPOSE 9000

ENV BUS_HOST=0.0.0.0
ENV BUS_PORT=9000

HEALTHCHECK --interval=15s --timeout=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:9000/health')" || exit 1

CMD ["python", "-c", "import uvicorn; from sahiixx_bus.server import app; uvicorn.run(app, host='0.0.0.0', port=9000, log_level='info')"]
