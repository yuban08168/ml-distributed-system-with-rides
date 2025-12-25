import json
import time
import uuid
from typing import List, Dict, Any, Generator
from concurrent.futures import ThreadPoolExecutor
import numpy as np
from sklearn.model_selection import ParameterGrid, ParameterSampler
from utils.redis_client import RedisClient
from config.settings import TASK_CONFIG

class TaskManager:
    """任务管理器 - 生成和分发任务"""
    
    def __init__(self):
        self.redis_client = RedisClient()
        self.task_counter = 0
        
    def generate_hyperparameters_grid(self) -> List[Dict[str, Any]]:
        """生成超参数网格（内存优化：使用生成器）"""
        
        # 定义超参数空间
        param_grid = {
            # 线性回归
            'linear': {
                'model_type': ['linear'],
                'fit_intercept': [True, False]
            },
            # 决策树
            'tree': {
                'model_type': ['tree'],
                'max_depth': [3, 5, 7, 10, 15, 20, None],
                'min_samples_split': [2, 5, 10],
                'min_samples_leaf': [1, 2, 4]
            },
            # 随机森林
            'forest': {
                'model_type': ['forest'],
                'n_estimators': [10, 50, 100, 200],
                'max_depth': [5, 10, 20, None],
                'min_samples_split': [2, 5, 10]
            },
            # 梯度提升
            'gradient': {
                'model_type': ['gradient'],
                'n_estimators': [50, 100, 200],
                'learning_rate': [0.01, 0.05, 0.1, 0.2],
                'max_depth': [3, 5, 7]
            }
        }
        
        all_params = []
        for model_type, params in param_grid.items():
            grid = ParameterGrid(params)
            for param_set in grid:
                # 将 model_type 字段从超参数中剥离，保持任务结构一致
                param_set = dict(param_set)
                param_set.pop('model_type', None)
                # 添加任务ID和其他元数据
                task = {
                    'task_id': f"task_{self.task_counter}",
                    'model_type': model_type,
                    'hyperparameters': param_set,
                    'created_at': time.time(),
                    'status': 'pending',
                    'retry_count': 0
                }
                all_params.append(task)
                self.task_counter += 1
                
                # 分批生成，避免内存爆炸
                if len(all_params) >= 1000:
                    yield all_params
                    all_params = []
        
        if all_params:
            yield all_params
    
    def generate_hyperparameters_random(self, n_tasks: int = 100000) -> Generator:
        """随机生成超参数（支持大规模任务）"""
        
        param_distributions = {
            'model_type': ['linear', 'tree', 'forest', 'gradient'],
            'max_depth': [3, 5, 7, 10, 15, 20, None],
            'min_samples_split': [2, 5, 10],
            'min_samples_leaf': [1, 2, 4],
            'n_estimators': [10, 50, 100, 200],
            'learning_rate': [0.01, 0.05, 0.1, 0.2],
            'fit_intercept': [True, False]
        }
        
        # 使用ParameterSampler生成随机参数
        param_sampler = ParameterSampler(param_distributions, n_iter=n_tasks)
        
        batch = []
        for i, params in enumerate(param_sampler):
            # 从随机采样结果中拆分出模型类型与具体超参数
            params = dict(params)
            model_type = params.get('model_type', 'tree')
            hyperparams = {k: v for k, v in params.items() if k != 'model_type'}
            task = {
                'task_id': f"task_{i}",
                'model_type': model_type,
                'hyperparameters': hyperparams,
                'created_at': time.time(),
                'status': 'pending',
                'retry_count': 0
            }
            batch.append(task)
            
            # 分批处理，每1000个任务一批
            if len(batch) >= 1000:
                yield batch
                batch = []
        
        if batch:
            yield batch
    
    def submit_tasks_batch(self, tasks_batch: List[Dict[str, Any]]):
        """批量提交任务"""
        success_count = 0
        failed_count = 0
        
        for task in tasks_batch:
            if self.redis_client.push_task(task):
                success_count += 1
            else:
                failed_count += 1
                
            # 每提交100个任务休息一下，避免Redis过载
            if (success_count + failed_count) % 100 == 0:
                time.sleep(0.01)
        
        return success_count, failed_count
    
    def submit_tasks_parallel(self, total_tasks: int = 10000):
        """并行提交任务"""
        print(f"开始生成并提交 {total_tasks} 个任务...")
        
        # 使用线程池并行提交
        with ThreadPoolExecutor(max_workers=4) as executor:
            futures = []
            
            # 分批生成和提交任务
            for batch in self.generate_hyperparameters_random(total_tasks):
                future = executor.submit(self.submit_tasks_batch, batch)
                futures.append(future)
            
            # 收集结果
            total_success = 0
            total_failed = 0
            
            for future in futures:
                success, failed = future.result()
                total_success += success
                total_failed += failed
                
                # 打印进度
                processed = total_success + total_failed
                if processed % 1000 == 0:
                    print(f"已提交: {processed}/{total_tasks}, "
                          f"成功: {total_success}, 失败: {total_failed}")
        
        print(f"任务提交完成！成功: {total_success}, 失败: {total_failed}")
        return total_success, total_failed
    
    def monitor_progress(self):
        """监控任务进度"""
        while True:
            stats = self.redis_client.get_queue_stats()
            print(f"任务队列: {stats['task_queue_size']}, "
                  f"结果队列: {stats['result_queue_size']}, "
                  f"活跃工作节点: {stats['active_workers']}")
            
            # 清理过期任务
            self.redis_client.cleanup_expired_tasks()
            
            time.sleep(10)  # 每10秒监控一次
