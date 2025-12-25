#!/bin/bash
# 分布式机器学习系统 - Docker部署脚本

set -e  # 遇到错误时退出脚本

# 颜色定义
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# 打印带颜色的信息
print_info() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

print_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

print_warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

print_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# 检查Docker是否安装
check_docker() {
    if ! command -v docker &> /dev/null; then
        print_error "Docker未安装，请先安装Docker"
        exit 1
    fi
    
    if ! command -v docker-compose &> /dev/null; then
        print_error "Docker Compose未安装，请先安装Docker Compose"
        exit 1
    fi
    
    print_success "Docker和Docker Compose已安装"
}

# 构建镜像
build_images() {
    print_info "正在构建Docker镜像..."
    docker-compose build
    print_success "镜像构建完成"
}

# 启动服务
start_services() {
    print_info "正在启动服务..."
    
    # 启动基础服务
    docker-compose up -d redis
    sleep 5
    
    # 检查Redis是否就绪
    if ! docker-compose exec redis redis-cli ping | grep -q "PONG"; then
        print_error "Redis启动失败"
        exit 1
    fi
    
    print_success "Redis启动成功"
    
    # 启动其他服务
    docker-compose up -d manager worker collector
    print_success "所有服务启动完成"
}

# 停止服务
stop_services() {
    print_info "正在停止服务..."
    docker-compose down
    print_success "服务已停止"
}

# 重启服务
restart_services() {
    print_info "正在重启服务..."
    docker-compose restart
    print_success "服务已重启"
}

# 查看日志
view_logs() {
    local service=$1
    if [ -z "$service" ]; then
        docker-compose logs -f
    else
        docker-compose logs -f "$service"
    fi
}

# 查看状态
view_status() {
    print_info "服务状态:"
    docker-compose ps
    
    echo ""
    print_info "容器资源使用:"
    docker stats --no-stream --format "table {{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}\t{{.NetIO}}\t{{.PIDs}}" \
        $(docker-compose ps -q)
}

# 扩展工作节点
scale_workers() {
    local count=$1
    if [ -z "$count" ]; then
        count=4
    fi
    
    print_info "扩展工作节点到 $count 个..."
    docker-compose up -d --scale worker=$count
    print_success "工作节点已扩展到 $count 个"
}

# 清理
cleanup() {
    print_warning "正在清理..."
    
    # 停止并删除容器
    docker-compose down -v
    
    # 删除未使用的镜像
    docker image prune -f
    
    # 删除未使用的卷
    docker volume prune -f
    
    print_success "清理完成"
}

# 备份数据
backup_data() {
    local backup_dir="backup_$(date +%Y%m%d_%H%M%S)"
    mkdir -p "$backup_dir"
    
    print_info "正在备份数据到 $backup_dir ..."
    
    # 备份结果
    if [ -d "results" ]; then
        cp -r results "$backup_dir/"
    fi
    
    # 备份日志
    if [ -d "logs" ]; then
        cp -r logs "$backup_dir/"
    fi
    
    # 备份Redis数据
    docker-compose exec redis redis-cli save
    docker cp "$(docker-compose ps -q redis)":/data "$backup_dir/redis_data"
    
    # 创建压缩包
    tar -czf "${backup_dir}.tar.gz" "$backup_dir"
    rm -rf "$backup_dir"
    
    print_success "数据已备份到 ${backup_dir}.tar.gz"
}

# 显示帮助
show_help() {
    echo "分布式机器学习系统 - Docker部署管理脚本"
    echo ""
    echo "使用方法: $0 [命令]"
    echo ""
    echo "命令:"
    echo "  build       构建Docker镜像"
    echo "  start       启动所有服务"
    echo "  stop        停止所有服务"
    echo "  restart     重启所有服务"
    echo "  logs [服务] 查看日志"
    echo "  status      查看服务状态"
    echo "  scale N     扩展工作节点到N个"
    echo "  clean       清理所有容器和卷"
    echo "  backup      备份数据"
    echo "  help        显示帮助信息"
    echo ""
    echo "示例:"
    echo "  $0 build        # 构建镜像"
    echo "  $0 start        # 启动服务"
    echo "  $0 scale 8      # 扩展到8个工作节点"
    echo "  $0 logs worker  # 查看工作节点日志"
}

# 主程序
main() {
    local command=$1
    local arg=$2
    
    case $command in
        build)
            check_docker
            build_images
            ;;
        start)
            check_docker
            start_services
            ;;
        stop)
            stop_services
            ;;
        restart)
            restart_services
            ;;
        logs)
            view_logs "$arg"
            ;;
        status)
            view_status
            ;;
        scale)
            scale_workers "$arg"
            ;;
        clean)
            cleanup
            ;;
        backup)
            backup_data
            ;;
        help|--help|-h)
            show_help
            ;;
        *)
            if [ -z "$command" ]; then
                show_help
            else
                print_error "未知命令: $command"
                echo ""
                show_help
                exit 1
            fi
            ;;
    esac
}

main "$@"
