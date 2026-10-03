FROM python:3.12-slim

WORKDIR /srv
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt && useradd -r -u 1500 viewer
COPY app ./app
ADD --chmod=755 https://github.com/SpaceManiac/SpacemanDMM/releases/download/suite-1.11/dmm-tools /usr/local/bin/dmm-tools
USER viewer
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8450", "--proxy-headers", "--forwarded-allow-ips", "*"]
