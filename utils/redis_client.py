import redis
import json
from typing import Optional, Any, Dict, List
import time
from contextlib import contextmanager
import pickle
from config.settings import REDIS_CONFIG, QUEUE_NAMES

class RedisClient:
    """Redis连接管理器（内存优化版）"""
    
    def __init__(self):
        self._connection_pool = redis.ConnectionPool(**REDIS_CONFIG)
        self._queues = QUEUE_NAMES
    
    @property
    def client(self):
        """获取Redis连接"""
        return redis.Redis(connection_pool=self._connection_pool)
    
    @contextmanager
    def get_connection(self):
        """上下文管理器获取连接"""
        conn = self.client
        try:
            yield conn
        finally:
            conn.close()
    
    def push_task(self, task_data: Dict[str, Any]) -> bool:
        """推送任务到队列（内存优化：使用zlib压缩）"""
        try:
            # 序列化并压缩任务数据
            import zlib
            task_bytes = pickle.dumps(task_data)
            compressed = zlib.compress(task_bytes)
            
            with self.get_connection() as conn:
                # 检查队列长度，避免内存溢出
                queue_len = conn.llen(self._queues['task_queue'])
                if queue_len >= 10000:  # 最大队列长度
                    return False
                
                # 使用流水线批量操作减少网络开销
                pipe = conn.pipeline()
                pipe.lpush(self._queues['task_queue'], compressed)
                # 设置任务超时时间
                task_id = f"task:{time.time()}:{hash(str(task_data))}"
                pipe.setex(f"{self._queues['task_progress']}:{task_id}", 
                         3600, "pending")
                pipe.execute()
                
                # 记录队列长度监控
                conn.zadd("queue:metrics", {self._queues['task_queue']: queue_len + 1})
                
                return True
        except Exception as e:
            print(f"推送任务失败: {e}")
            return False
    
    def pop_task(self, timeout: int = 30) -> Optional[Dict[str, Any]]:
        """从队列获取任务（阻塞式）"""
        try:
            with self.get_connection() as conn:
                # 使用brpop阻塞获取任务
                result = conn.brpop(self._queues['task_queue'], timeout=timeout)
                if result:
                    _, compressed_data = result
                    
                    # 解压数据
                    import zlib
                    decompressed = zlib.decompress(compressed_data)
                    task_data = pickle.loads(decompressed)
                    
                    return task_data
                return None
        except Exception as e:
            print(f"获取任务失败: {e}")
            return None
    
    def push_result(self, result_data: Dict[str, Any]) -> bool:
        """推送结果到结果队列"""
        try:
            # 使用JSON序列化结果（节省内存）
            import json
            result_str = json.dumps(result_data)
            
            with self.get_connection() as conn:
                # 限制结果队列大小
                queue_len = conn.llen(self._queues['result_queue'])
                if queue_len >= 5000:  # 结果队列限制
                    return False
                
                conn.lpush(self._queues['result_queue'], result_str)
                return True
        except Exception as e:
            print(f"推送结果失败: {e}")
            return False
    
    def pop_result(self) -> Optional[Dict[str, Any]]:
        """获取结果"""
        try:
            with self.get_connection() as conn:
                result = conn.rpop(self._queues['result_queue'])
                if result:
                    return json.loads(result)
                return None
        except Exception as e:
            print(f"获取结果失败: {e}")
            return None
    
    def register_worker(self, worker_id: str, worker_info: Dict[str, Any]):
        """注册工作节点"""
        with self.get_connection() as conn:
            conn.hset(self._queues['worker_status'], worker_id, 
                     json.dumps({
                         **worker_info,
                         'last_heartbeat': time.time(),
                         'status': 'running'
                     }))
    
    def update_worker_heartbeat(self, worker_id: str):
        """更新工作节点心跳"""
        with self.get_connection() as conn:
            worker_data = conn.hget(self._queues['worker_status'], worker_id)
            if worker_data:
                data = json.loads(worker_data)
                data['last_heartbeat'] = time.time()
                conn.hset(self._queues['worker_status'], worker_id, json.dumps(data))
    
    def cleanup_expired_tasks(self):
        """清理过期任务"""
        with self.get_connection() as conn:
            # 查找过期的任务
            keys = conn.keys(f"{self._queues['task_progress']}:*")
            for key in keys:
                if not conn.exists(key):
                    conn.delete(key)
    
    def get_queue_stats(self) -> Dict[str, Any]:
        """获取队列统计信息"""
        with self.get_connection() as conn:
            return {
                'task_queue_size': conn.llen(self._queues['task_queue']),
                'result_queue_size': conn.llen(self._queues['result_queue']),
                'active_workers': conn.hlen(self._queues['worker_status'])
            }

    def reset_queues(self, clear_workers: bool = False):
        """清空任务和结果队列，避免不同运行之间相互干扰"""
        with self.get_connection() as conn:
            conn.delete(self._queues['task_queue'])
            conn.delete(self._queues['result_queue'])
            # 仅在需要时清理 worker 状态，默认保留，避免影响长驻 worker 模式
            if clear_workers:
                conn.delete(self._queues['worker_status'])

