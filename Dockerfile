FROM python:3.12-slim

WORKDIR /app

# 先装依赖（利用构建缓存）
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# 拷贝应用代码
COPY free_proxy/ ./free_proxy/
COPY conf/ ./conf/

EXPOSE 8899 8891

CMD ["python", "-m", "free_proxy", "-c", "/app/conf/config.yaml"]