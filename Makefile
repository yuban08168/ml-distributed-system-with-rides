.PHONY: build start stop restart logs status scale clean backup help

# 默认目标
.DEFAULT_GOAL := help

# 显示帮助信息
help:
	@echo "分布式机器学习系统 - Makefile 管理命令"
	@echo ""
	@echo "可用命令:"
	@echo "  make build          构建Docker镜像"
	@echo "  make start          启动所有服务"
	@echo "  make stop           停止所有服务"
	@echo "  make restart        重启所有服务"
	@echo "  make logs [SERVICE] 查看日志（可指定服务名）"
	@echo "  make status         查看服务状态"
	@echo "  make scale N=4      扩展工作节点（N=数量）"
	@echo "  make clean          清理所有容器和卷"
	@echo "  make backup         备份数据"
	@echo "  make test           运行测试"
	@echo "  make monitor        启动监控面板"
	@echo ""

# 构建镜像
build:
	@echo "正在构建Docker镜像..."
	docker-compose build
	@echo "镜像构建完成！"

# 启动服务
start:
	@echo "正在启动服务..."
	docker-compose up -d redis
	@sleep 5
	@docker-compose up -d manager worker collector
	@echo "服务启动完成！"
	@echo ""
	@echo "访问以下地址:"
	@echo "  - Redis: localhost:6379"
	@echo "  - 监控: localhost:8080 (如果启用)"

# 停止服务
stop:
	@echo "正在停止服务..."
	docker-compose down
	@echo "服务已停止"

# 重启服务
restart: stop start
	@echo "服务已重启"

# 查看日志
logs:
ifdef SERVICE
	@docker-compose logs -f $(SERVICE)
else
	@docker-compose logs -f
endif

# 查看状态
status:
	@docker-compose ps
	@echo ""
	@echo "容器资源使用情况:"
	@docker stats --no-stream --format "table {{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}\t{{.NetIO}}" $(docker-compose ps -q)

# 扩展工作节点
scale:
ifndef N
	$(error 请指定工作节点数量，例如: make scale N=4)
endif
	@echo "扩展工作节点到 $(N) 个..."
	@docker-compose up -d --scale worker=$(N)
	@echo "工作节点已扩展到 $(N) 个"

# 清理
clean:
	@echo "正在清理..."
	@docker-compose down -v
	@docker image prune -f
	@docker volume prune -f
	@echo "清理完成"

# 备份数据
backup:
	@./deploy.sh backup

# 运行测试
test:
	@echo "运行测试..."
	@docker-compose run --rm manager python -m pytest tests/ -v

# 监控面板
monitor:
	@echo "启动监控面板..."
	@docker-compose up -d prometheus grafana
	@echo ""
	@echo "监控面板已启动:"
	@echo "  - Prometheus: http://localhost:9090"
	@echo "  - Grafana: http://localhost:3000 (admin/admin)"
	@echo ""
	@echo "等待服务启动..."
	@sleep 10
	@echo "请在Grafana中导入仪表板"

# 一键部署（开发环境）
dev: build start
	@echo "开发环境部署完成！"
	@echo "查看日志: make logs"
	@echo "查看状态: make status"

# 生产环境部署
prod:
	@echo "生产环境部署..."
	@docker-compose -f docker-compose.yml -f docker-compose.prod.yml up -d
	@echo "生产环境部署完成！"
