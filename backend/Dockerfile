FROM python:3.11-slim

WORKDIR /app

# 换用阿里云镜像源加速 apt，然后安装系统依赖
RUN sed -i 's/deb.debian.org/mirrors.aliyun.com/g' /etc/apt/sources.list.d/debian.sources \
    && apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libmagic1 \
    nodejs \
    npm \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies (with Aliyun mirror for pip)
# CPU-only PyTorch（先装 torch 避免 pip 拉 GPU 版，同时也避免 sentence-transformers 拖整个 CUDA 工具链）
COPY requirements.txt .
RUN pip config set global.index-url https://mirrors.aliyun.com/pypi/simple/ \
    && pip install --default-timeout=600 --retries 5 torch==2.5.1 --index-url https://download.pytorch.org/whl/cpu \
    && pip install --default-timeout=600 --retries 5 -r requirements.txt

# Copy application code
COPY . .

# 全局安装 MCP 工具包（放在 COPY . 之后，避免破坏 Python 依赖的层缓存）
RUN npm install -g @modelcontextprotocol/server-filesystem

# Create data directories
RUN mkdir -p /app/data/uploads /app/output

# ── 设置 HuggingFace 镜像（加速国内下载） ──
ENV HF_ENDPOINT=https://hf-mirror.com

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]