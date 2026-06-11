FROM python:3.11-slim

# kubectl
RUN apt-get update && apt-get install -y curl && \
    curl -LO "https://dl.k8s.io/release/$(curl -Ls https://dl.k8s.io/release/stable.txt)/bin/linux/amd64/kubectl" && \
    install -o root -g root -m 0755 kubectl /usr/local/bin/kubectl && \
    rm kubectl && apt-get clean

WORKDIR /app

COPY shared/          ./shared/
COPY manager/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY manager/         ./manager/

ENV PYTHONPATH=/app
EXPOSE 8080

CMD ["python", "-m", "manager.main"]
