FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends git && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY pyproject.toml README.md ./
COPY agents/ ./agents/
COPY integrations/ ./integrations/
COPY rules/ ./rules/
COPY *.py ./
RUN pip install --no-cache-dir '.[dashboard,scanners]' && useradd --create-home reviewer && mkdir -p /app/.data && chown -R reviewer:reviewer /app
USER reviewer
EXPOSE 8501
CMD ["streamlit", "run", "app.py", "--server.address=0.0.0.0"]
