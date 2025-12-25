#!/bin/bash

echo "🚀 分布式机器学习系统 - 一键部署脚本"
echo "========================================"

# 1. 克隆项目（如果是新服务器）
if [ ! -d "ml-docker-system" ]; then
    echo "📥 下载项目代码..."
    # 这里假设你已经有代码，实际使用时可以从Git仓库克隆
    mkdir -p ml-docker-system
    echo "✅ 项目目录创建完成"
fi

cd ml-docker-system

# 2. 创建必要的目录
echo "📁 创建目录结构..."
mkdir -p results logs data

# 3. 给脚本执行权限
chmod +x deploy.sh
chmod +x run.py

# 4. 构建Docker镜像
echo "🐳 构建Docker镜像..."
docker-compose build

# 5. 启动服务
echo "🚀 启动服务..."
docker-compose up -d redis

# 等待Redis启动
echo "⏳ 等待Redis启动..."
sleep 5

# 检查Redis状态
if docker-compose exec redis redis-cli ping | grep -q "PONG"; then
    echo "✅ Redis启动成功"
else
    echo "❌ Redis启动失败"
    exit 1
fi

# 启动其他服务
docker-compose up -d manager worker collector

# 6. 显示状态
echo ""
echo "📊 部署完成！系统状态："
docker-compose ps

echo ""
echo "🌐 访问信息："
echo "  - Redis管理：redis-cli -h localhost -p 6379"
echo "  - 查看日志：docker-compose logs -f"
echo "  - 停止服务：docker-compose down"
echo ""
echo "📈 扩展工作节点："
echo "  docker-compose up -d --scale worker=8"
echo ""
echo "📁 数据目录："
echo "  - 结果文件：./results/"
echo "  - 日志文件：./logs/"
