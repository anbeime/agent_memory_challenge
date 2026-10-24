FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
# 健康检查（端口跟随 $PORT / $AML_PORT）
HEALTHCHECK CMD sh -c 'python -c "import urllib.request,os; p=os.environ.get(\"PORT\") or os.environ.get(\"AML_PORT\",\"8080\"); urllib.request.urlopen(\"http://127.0.0.1:%s/health\"%p)"' || exit 1
EXPOSE 8080
CMD ["sh", "-c", "uvicorn app:app --host 0.0.0.0 --port ${PORT:-${AML_PORT:-8080}}"]
