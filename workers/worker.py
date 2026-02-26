import time
import json
import signal
import sys
from typing import Dict, Any, Optional, Callable
import psutil
import os
import importlib

from utils.redis_client import RedisClient


def _load_executor() -> Callable[[Dict[str, Any]], Dict[str, Any]]:
    """根据环境变量动态加载任务执行函数。

    默认从 workers.executor:execute_task 加载，
    使用者可以通过设置环境变量 MLDS_EXECUTOR 来替换，
    例如：

    MLDS_EXECUTOR="my_project.executor:execute_task"
    """

    target = os.getenv("MLDS_EXECUTOR", "workers.executor:execute_task")
    if ":" not in target:
        raise RuntimeError(
            f"无效的 MLDS_EXECUTOR 配置: {target}，应为 'module:func' 形式"
        )

    module_name, func_name = target.split(":", 1)
    module = importlib.import_module(module_name)
    func = getattr(module, func_name, None)
    if func is None:
        raise RuntimeError(
            f"在模块 {module_name} 中未找到函数 {func_name}，"
            f"请确认自定义执行器实现无误。"
        )
    return func


class MLWorker:
    """通用任务工作节点。

    该类不再内置具体的机器学习训练逻辑，而是通过可插拔的
    执行器函数来完成实际计算。调度系统只负责：
    - 从 Redis 拉取任务
    - 调用执行器得到结果
    - 包装通用元数据（task_id/job_id/worker_id/memory 等）并推送结果队列
    """

    def __init__(self, worker_id: str = None):
        self.worker_id = worker_id or f"worker_{os.getpid()}_{int(time.time())}"
        self.redis_client = RedisClient()
        self.is_running = True

        # 加载任务执行器
        self.executor = _load_executor()

        # 注册信号处理
        signal.signal(signal.SIGINT, self.signal_handler)
        signal.signal(signal.SIGTERM, self.signal_handler)

        # 注册工作节点
        self.register_worker()

        # 内存监控
        self.memory_limit_mb = 500  # 内存限制500MB
        self.last_gc_time = time.time()
    
    def register_worker(self):
        """注册工作节点到Redis"""
        worker_info = {
            'pid': os.getpid(),
            'hostname': os.uname().nodename if hasattr(os, 'uname') else 'unknown',
            'start_time': time.time(),
            'processed_tasks': 0,
            'memory_usage': 0
        }
        self.redis_client.register_worker(self.worker_id, worker_info)
    
    def update_heartbeat(self):
        """更新心跳"""
        memory_info = psutil.Process().memory_info()
        worker_info = {
            'pid': os.getpid(),
            'memory_usage_mb': memory_info.rss / 1024 / 1024,
            'last_update': time.time(),
            'processed_tasks': getattr(self, 'processed_count', 0)
        }
        self.redis_client.update_worker_heartbeat(self.worker_id)
    
    def signal_handler(self, signum, frame):
        """处理退出信号"""
        print(f"\n工作节点 {self.worker_id} 收到退出信号")
        self.is_running = False
    
    def check_memory_usage(self) -> bool:
        """检查内存使用情况"""
        process = psutil.Process(os.getpid())
        memory_mb = process.memory_info().rss / 1024 / 1024
        
        # 定期垃圾回收
        current_time = time.time()
        if current_time - self.last_gc_time > 60:  # 每60秒一次GC
            # gc.collect()
            self.last_gc_time = current_time
        
        # 检查是否超过内存限制
        if memory_mb > self.memory_limit_mb:
            print(f"警告：内存使用过高 ({memory_mb:.2f} MB)")
            return False
        
        return True

    def execute_task(self, task: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """调用用户自定义执行器并包装通用结果。

        执行器自身只关心 task 的业务含义，这里只负责：
        - 做基础的内存检查
        - 记录执行时间
        - 为结果补充调度层面的元数据
        """

        try:
            # 检查内存使用
            if not self.check_memory_usage():
                return None

            start_time = time.time()
            user_result = self.executor(task) or {}
            elapsed = time.time() - start_time

            if not isinstance(user_result, dict):
                raise TypeError("执行器返回值必须是 dict，可被 JSON 序列化")

            base = {
                'task_id': task.get('task_id'),
                'job_id': task.get('job_id'),
                'worker_id': self.worker_id,
                'completed_at': time.time(),
                'memory_usage_mb': psutil.Process().memory_info().rss / 1024 / 1024,
                'executor_elapsed': elapsed,
            }

            # 用户结果优先，调度元数据补充其余键
            merged = {**base, **user_result}
            return merged

        except Exception as e:
            print(f"任务执行失败: {e}")
            return None
    
    def run(self):
        """运行工作节点"""
        print(f"工作节点 {self.worker_id} 启动...")
        processed_count = 0
        idle_rounds = 0
        
        while self.is_running:
            try:
                # 更新心跳
                if processed_count % 10 == 0:
                    self.update_heartbeat()
                
                # 获取任务
                task = self.redis_client.pop_task(timeout=5)
                
                if task:
                    idle_rounds = 0
                    print(f"工作节点 {self.worker_id} 处理任务: {task.get('task_id')}")
                    
                    # 执行任务
                    result = self.execute_task(task)
                    
                    if result:
                        # 提交结果
                        if self.redis_client.push_result(result):
                            processed_count += 1
                            print(f"工作节点 {self.worker_id} 完成任务: {result.get('task_id')}")
                        else:
                            print(f"提交结果失败: {result.get('task_id')}")
                    else:
                        print(f"训练失败: {task.get('task_id')}")
                else:
                    # 连续多次拿不到任务且队列已空，则认为当前轮次任务已结束，自动退出
                    idle_rounds += 1
                    if idle_rounds >= 12:  # 约 12*5s = 60s 空闲
                        stats = self.redis_client.get_queue_stats()
                        if stats['task_queue_size'] == 0:
                            print(f"工作节点 {self.worker_id} 长时间空闲且任务队列为空，自动退出")
                            break
                
                # 稍微休息，避免CPU占用过高
                # time.sleep(0.1)
                
            except KeyboardInterrupt:
                break
            except Exception as e:
                print(f"工作节点 {self.worker_id} 错误: {e}")
                time.sleep(1)
        
        print(f"工作节点 {self.worker_id} 停止，共处理 {processed_count} 个任务")
