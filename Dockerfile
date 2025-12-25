FROM python:3.9-slim

# 安装系统依赖
RUN apt-get update && apt-get install -y \
    redis-server \
    gcc \
    g++ \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 复制代码
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# 创建非root用户
RUN useradd -m -u 1000 worker
RUN chown -R worker:worker /app
USER worker

# 启动脚本
CMD ["python", "run.py", "all", "--tasks", "1000", "--workers", "4"]
