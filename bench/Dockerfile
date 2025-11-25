FROM python:3.12-slim

WORKDIR /app

RUN pip install requests

COPY query_bench.py .

CMD ["python", "query_bench.py"]
