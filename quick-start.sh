#!/usr/bin/env bash
set -e

echo "🚀 分布式机器学习系统 - Quick Start (CLI 优先)"
echo "============================================="

# 0) 进入项目根目录
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

# 1) Python 虚拟环境与依赖
if [ ! -d ".venv" ]; then
    echo "📦 创建虚拟环境 .venv"
    python -m venv .venv
fi
source .venv/bin/activate
echo "📦 安装依赖"
pip install -r requirements.txt
pip install -e .  # 安装生成 ml-ds 命令

# 2) 启动本地 Redis（如已启动可跳过）
if command -v redis-server >/dev/null 2>&1; then
    echo "🧩 启动本地 Redis（后台）"
    if [ -f "redis.conf" ]; then
        redis-server redis.conf &
    else
        redis-server &
    fi
    sleep 2
else
    echo "⚠️ 未检测到 redis-server，请自行启动或指定 --redis-url"
fi

# 3) 运行一键所有组件（可通过环境变量覆盖）
TASKS="${TASKS:-100}"
WORKERS="${WORKERS:-2}"
MONITOR="${MONITOR:-1}"

MONITOR_FLAG=""
if [ "$MONITOR" = "1" ]; then
    MONITOR_FLAG="--monitor"
fi

echo "🚀 启动：ml-ds all --tasks $TASKS -w $WORKERS $MONITOR_FLAG"
ml-ds all --tasks "$TASKS" -w "$WORKERS" $MONITOR_FLAG

echo ""
echo "✅ 已启动所有组件。结果将写入 results/ 下的 CSV 文件。"
echo "🔧 自定义示例："
echo "  - TASKS=200 WORKERS=4 MONITOR=1 ./quick-start.sh"
echo "  - 使用远程 Redis：ml-ds all --redis-url redis://your-host:6379/0 --tasks 100 -w 2 --monitor"
echo "  - 单独跑 worker：ml-ds worker --redis-url redis://your-host:6379/0 -c 8"
