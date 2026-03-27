FROM python:3.11-slim

WORKDIR /app

COPY mcp_server/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY mcp_server/mcp_server.py .
COPY brains.yaml .
COPY brains/ ./brains/

EXPOSE 8000

CMD ["python", "mcp_server.py"]
