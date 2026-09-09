FROM python:3.12-slim

WORKDIR /opt/my-ad/converter
COPY scripts/dataset-converter/convert.py /opt/my-ad/converter/convert.py

ENTRYPOINT ["python", "/opt/my-ad/converter/convert.py"]
