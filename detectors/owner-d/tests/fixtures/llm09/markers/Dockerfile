FROM public.ecr.aws/docker/library/python:3.12-slim
COPY . /app
RUN pip install -r /app/requirements.txt
CMD ["opentelemetry-instrument", "python", "/app/server.py"]
