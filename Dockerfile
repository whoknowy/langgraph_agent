# 智能航空客服系统镜像（Flask Web + LangGraph 服务共用一张图，compose 里用不同 command 启动）
# 构建：docker build -t flight-agent .
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    TZ=Asia/Shanghai

WORKDIR /app

# 依赖先行（利用层缓存）
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# 应用代码 + 前端构建产物（Flask 只服务 frontend/dist）
COPY langgraph.json config.py web_app.py chat_web_service.py multi_agent_customer_service.py ./
COPY agents ./agents
COPY services ./services
COPY skills ./skills
COPY frontend/dist ./frontend/dist

# 数据与密钥建议以卷挂载（./data 与 ./keys），镜像内不落库
EXPOSE 5000 2024

# 默认起 Flask（生产 WSGI）；LangGraph 服务在 compose 里覆盖 command
CMD ["waitress-serve", "--listen=0.0.0.0:5000", "web_app:app"]
