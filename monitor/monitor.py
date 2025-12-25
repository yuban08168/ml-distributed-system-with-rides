import time
import json
from utils.redis_client import RedisClient
import psutil

class SystemMonitor:
    """系统监控器"""
    
    def __init__(self):
        self.redis_client = RedisClient()
    
    def collect_metrics(self):
        """收集系统指标"""
        # Redis指标
        redis_stats = self.redis_client.get_queue_stats()
        
        # 系统指标
        cpu_percent = psutil.cpu_percent(interval=1)
        memory = psutil.virtual_memory()
        disk = psutil.disk_usage('/')
        
        metrics = {
            'timestamp': time.time(),
            'redis': redis_stats,
            'system': {
                'cpu_percent': cpu_percent,
                'memory_percent': memory.percent,
                'memory_used_gb': memory.used / 1024 / 1024 / 1024,
                'disk_percent': disk.percent,
                'disk_used_gb': disk.used / 1024 / 1024 / 1024
            }
        }
        
        return metrics
    
    def monitor(self, interval=10):
        """持续监控"""
        print("系统监控启动...")
        
        try:
            while True:
                metrics = self.collect_metrics()
                
                # 打印监控信息
                print("\n" + "="*50)
                print(f"时间: {time.strftime('%Y-%m-%d %H:%M:%S')}")
                print(f"CPU使用率: {metrics['system']['cpu_percent']}%")
                print(f"内存使用率: {metrics['system']['memory_percent']}%")
                print(f"任务队列: {metrics['redis']['task_queue_size']}")
                print(f"结果队列: {metrics['redis']['result_queue_size']}")
                print(f"活跃工作节点: {metrics['redis']['active_workers']}")
                print("="*50)
                
                # 保存到Redis
                with self.redis_client.get_connection() as conn:
                    conn.lpush('system:metrics', json.dumps(metrics))
                    # 只保留最近100条记录
                    conn.ltrim('system:metrics', 0, 99)
                
                time.sleep(interval)
                
        except KeyboardInterrupt:
            print("\n监控停止")
