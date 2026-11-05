FROM python:3.12-slim@sha256:4840d514c583a4d3d8cad3940e68dee6f0f6f3635c12fcd0a3d74c21a5b93a54
RUN pip install --no-cache-dir boto3==1.43.117 requests==2.34.2
COPY bootstrap.py /app/bootstrap.py
USER 65532:65532
ENTRYPOINT ["python", "/app/bootstrap.py"]
