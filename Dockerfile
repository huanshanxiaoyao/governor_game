# 固定基础镜像版本，保证两边拉取的镜像完全一致
FROM python:3.10-slim

# 设置工作目录（容器内固定路径，两边无差异）
WORKDIR /app

# 复制依赖文件，先装依赖（利用Docker缓存，加快构建）
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple

# 复制项目代码到容器（开发时通过挂载覆盖，这里是兜底）
COPY . .

# 启动命令（可通过docker-compose覆盖，更灵活）
CMD ["python", "app.py"]
