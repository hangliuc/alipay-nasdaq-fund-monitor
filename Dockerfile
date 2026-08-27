FROM python:3.11-slim

# 安装 cron + 字体工具，使用与 macOS 相同的 STHeiti Medium 字体
RUN apt-get update && \
    apt-get install -y --no-install-recommends cron fontconfig && \
    mkdir -p /usr/share/fonts/custom && \
    rm -rf /var/lib/apt/lists/*

COPY fonts/STHeiti-Medium.ttc /usr/share/fonts/custom/STHeiti-Medium.ttc
RUN fc-cache -f

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# 数据和图片持久化目录（项目说明文档不作为运行时输出目录）
RUN mkdir -p /app/data/cards

# cron 定时任务：每天 7:16 北京时间执行
# 容器内设置为 Asia/Shanghai 时区
ENV TZ=Asia/Shanghai
RUN ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && echo $TZ > /etc/timezone

# 生成 crontab（环境变量通过 env 文件注入）
RUN echo '16 7 * * * cd /app && /usr/local/bin/python main.py >> /var/log/qdii-quota-radar.log 2>&1' > /etc/cron.d/qdii-quota-radar && \
    chmod 0644 /etc/cron.d/qdii-quota-radar && \
    crontab /etc/cron.d/qdii-quota-radar && \
    touch /var/log/qdii-quota-radar.log

CMD ["sh", "-c", "printenv > /etc/environment && cron && tail -f /var/log/qdii-quota-radar.log"]
