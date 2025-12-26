#!/usr/bin/env python3
"""
分布式机器学习任务调度系统
"""
import os
import sys
import argparse
import signal
import time
from pathlib import Path

# 添加项目路径到Python路径
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

from manager.task_manager import TaskManager
from workers.worker import MLWorker
from manager.result_collector import ResultCollector
from monitor.monitor import SystemMonitor
from utils.redis_client import RedisClient
from config.settings import REDIS_CONFIG

def setup_environment():
    """设置环境变量"""
    os.environ.setdefault('PYTHONPATH', str(project_root))
    os.environ.setdefault('LOG_LEVEL', 'INFO')

def start_redis_check():
    """检查Redis连接"""
    try:
        import redis
        # 统一使用配置文件中的 Redis 参数，避免本地与 Docker 默认不一致
        r = redis.Redis(**REDIS_CONFIG)
        r.ping()
        print(f"✅ Redis连接成功: {REDIS_CONFIG['host']}:{REDIS_CONFIG['port']}")
        return True
    except Exception as e:
        print(f"❌ Redis连接失败: {e}")
        return False

def start_manager(args):
    """启动任务管理器"""
    print(f"🚀 启动任务管理器，生成 {args.tasks} 个任务...")
    
    if not start_redis_check():
        return
    
    # 每次启动前清空历史任务和结果，避免新一轮运行读到旧数据
    redis_client = RedisClient()
    redis_client.reset_queues()

    manager = TaskManager()
    
    # 注册信号处理
    def signal_handler(signum, frame):
        print("\n📦 正在保存任务状态...")
        manager.save_state()
        sys.exit(0)
    
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    manager.submit_tasks_parallel(args.tasks)
    print("✅ 所有任务已提交到队列")

def start_worker(args):
    """启动工作节点"""
    print(f"👷 启动工作节点: {args.worker_id}")
    
    if not start_redis_check():
        return
    
    worker = MLWorker(args.worker_id)
    worker.run()

def start_collector(args):
    """启动结果收集器"""
    print("📊 启动结果收集器...")
    
    if not start_redis_check():
        return
    
    collector = ResultCollector()
    collector.collect_results(args.max_results)

def start_monitor(args):
    """启动系统监控"""
    print("📈 启动系统监控...")
    
    if not start_redis_check():
        return
    
    monitor = SystemMonitor()
    monitor.monitor(args.interval)

def start_all(args):
    """启动所有组件（本地多进程开发测试）"""
    print("=" * 60)
    print("🚀 启动分布式机器学习任务调度系统")
    print("=" * 60)
    
    if not start_redis_check():
        print("❌ Redis连接失败，请检查Redis服务")
        return

    # all 模式下，每次启动前清空任务/结果队列，确保本次运行环境干净
    redis_client = RedisClient()
    redis_client.reset_queues()
    
    # 导入多进程
    import multiprocessing
    import atexit
    
    processes = []
    
    def cleanup():
        """清理进程"""
        for p in processes:
            if p.is_alive():
                p.terminate()
                p.join()
    
    atexit.register(cleanup)
    
    # 启动任务管理器进程
    print("1. 启动任务管理器...")
    manager_proc = multiprocessing.Process(
        target=start_manager,
        args=(argparse.Namespace(tasks=args.tasks),)
    )
    manager_proc.start()
    processes.append(manager_proc)
    time.sleep(2)
    
    # 启动多个工作节点进程
    print(f"2. 启动 {args.workers} 个工作节点...")
    for i in range(args.workers):
        worker_proc = multiprocessing.Process(
            target=start_worker,
            args=(argparse.Namespace(worker_id=f"worker-{i+1}"),)
        )
        worker_proc.start()
        processes.append(worker_proc)
        time.sleep(0.5)
    
    # 启动结果收集器进程
    print("3. 启动结果收集器...")
    collector_proc = multiprocessing.Process(
        target=start_collector,
        args=(argparse.Namespace(max_results=args.tasks),)
    )
    collector_proc.start()
    processes.append(collector_proc)
    
    # 启动监控进程（可选）
    if args.monitor:
        print("4. 启动系统监控...")
        monitor_proc = multiprocessing.Process(
            target=start_monitor,
            args=(argparse.Namespace(interval=10),)
        )
        monitor_proc.start()
        processes.append(monitor_proc)
    
    print("\n✅ 所有组件已启动！")
    print("📋 运行状态:")
    print(f"   - 任务数量: {args.tasks}")
    print(f"   - 工作节点: {args.workers}")
    print(f"   - 监控: {'启用' if args.monitor else '禁用'}")
    print("\n🛑 按 Ctrl+C 停止所有组件...")
    
    try:
        # 等待所有进程
        for p in processes:
            p.join()
    except KeyboardInterrupt:
        print("\n🛑 收到停止信号，正在停止所有组件...")
        cleanup()

def main():
    parser = argparse.ArgumentParser(
        description='分布式机器学习任务调度系统',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
使用示例:
  # 启动完整系统 (开发模式)
  python run.py all --tasks 100 --workers 2 --monitor
  
  # 仅启动任务管理器
  python run.py manager --tasks 1000
  
  # 启动工作节点
  python run.py worker --worker-id worker-1
  
  # 启动结果收集器
  python run.py collector --max-results 1000
  
  # 启动监控
  python run.py monitor --interval 5
        """
    )
    
    subparsers = parser.add_subparsers(dest='command', help='子命令')
    
    # all命令
    all_parser = subparsers.add_parser('all', help='启动所有组件')
    all_parser.add_argument('--tasks', type=int, default=100, help='任务数量')
    all_parser.add_argument('--workers', type=int, default=2, help='工作节点数量')
    all_parser.add_argument('--monitor', action='store_true', help='启用监控')
    
    # manager命令
    manager_parser = subparsers.add_parser('manager', help='启动任务管理器')
    manager_parser.add_argument('--tasks', type=int, default=1000, help='任务数量')
    
    # worker命令
    worker_parser = subparsers.add_parser('worker', help='启动工作节点')
    worker_parser.add_argument('--worker-id', required=True, help='工作节点ID')
    
    # collector命令
    collector_parser = subparsers.add_parser('collector', help='启动结果收集器')
    collector_parser.add_argument('--max-results', type=int, help='最大收集结果数')
    
    # monitor命令
    monitor_parser = subparsers.add_parser('monitor', help='启动系统监控')
    monitor_parser.add_argument('--interval', type=int, default=10, help='监控间隔(秒)')
    
    args = parser.parse_args()
    
    setup_environment()
    
    if args.command == 'all':
        start_all(args)
    elif args.command == 'manager':
        start_manager(args)
    elif args.command == 'worker':
        start_worker(args)
    elif args.command == 'collector':
        start_collector(args)
    elif args.command == 'monitor':
        start_monitor(args)
    else:
        parser.print_help()

if __name__ == '__main__':
    main()
