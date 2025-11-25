FROM python:3.12-slim

WORKDIR /app

RUN pip install prometheus_client requests

COPY metrics_gen.py .

EXPOSE 8000

CMD ["python", "-u", "metrics_gen.py"]
