FROM python:3.10-slim-bookworm

ENV TZ=Asia/Shanghai \
    LANG=C.UTF-8 \
    LC_ALL=C.UTF-8 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple --extra-index-url https://pypi.org/simple

COPY backend/ .

CMD ["python", "manage.py", "runserver", "0.0.0.0:8000"]
