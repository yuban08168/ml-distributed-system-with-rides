import time
import json
import signal
import sys
import gc
from typing import Dict, Any, Optional
import numpy as np
import pandas as pd
from sklearn.model_selection import cross_validate, train_test_split
from sklearn.linear_model import LinearRegression, Ridge
from sklearn.tree import DecisionTreeRegressor
from sklearn.ensemble import RandomForestRegressor, GradientBoostingRegressor
from sklearn.metrics import mean_squared_error, r2_score
import psutil
import os

from utils.redis_client import RedisClient
from config.settings import MODEL_CONFIG, DATA_DIR

class MLWorker:
    """机器学习工作节点（内存优化版）"""
    
    def __init__(self, worker_id: str = None):
        self.worker_id = worker_id or f"worker_{os.getpid()}_{int(time.time())}"
        self.redis_client = RedisClient()
        self.data = None  # 延迟加载数据
        self.is_running = True
        
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
    
    def load_data(self):
        """加载数据集（延迟加载，减少内存占用）"""
        if self.data is None:
            print("加载波士顿房价数据集...")

            # 优先尝试使用 sklearn 内置数据集（旧版本可用）
            X = None
            y = None
            feature_names = None
            try:
                from sklearn.datasets import load_boston  # 延迟导入，兼容新版本 sklearn
                boston = load_boston()

                X = pd.DataFrame(boston.data, columns=boston.feature_names)
                y = pd.Series(boston.target, name='MEDV')
                feature_names = boston.feature_names.tolist()
            except Exception as e:
                print(f"使用 sklearn.load_boston 失败，将尝试从本地 CSV 加载: {e}")
                csv_path = DATA_DIR / 'boston_housing.csv'
                if not csv_path.exists():
                    raise RuntimeError(
                        f"无法加载 Boston 数据集，请在 'data' 目录中放入 'boston_housing.csv' 文件。期待路径: {csv_path}"
                    )

                # 优先尝试普通带表头的 CSV，要求包含 MEDV 列
                df = None
                try:
                    df = pd.read_csv(csv_path)
                except Exception:
                    df = None

                if df is not None and 'MEDV' in df.columns:
                    X = df.drop(columns=['MEDV'])
                    y = df['MEDV']
                    feature_names = X.columns.tolist()
                else:
                    # 兼容无表头、以空白分隔的原始 Boston 数据格式
                    df_raw = pd.read_csv(csv_path, header=None, delim_whitespace=True)
                    if df_raw.shape[1] != 14:
                        raise RuntimeError(
                            "无法识别本地 Boston 数据集格式：既没有 MEDV 列，也不是每行 14 列的原始数据。"
                        )

                    feature_names = [
                        'CRIM', 'ZN', 'INDUS', 'CHAS', 'NOX', 'RM',
                        'AGE', 'DIS', 'RAD', 'TAX', 'PTRATIO', 'B',
                        'LSTAT', 'MEDV'
                    ]
                    df_raw.columns = feature_names
                    X = df_raw.drop(columns=['MEDV'])
                    y = df_raw['MEDV']

            # 转换为 DataFrame/Series 后统一做内存优化
            for col in X.columns:
                if X[col].dtype == 'float64':
                    X[col] = X[col].astype('float32')
                elif X[col].dtype == 'int64':
                    X[col] = X[col].astype('int32')

            self.data = {
                'X': X,
                'y': y,
                'feature_names': feature_names,
                'target_name': 'MEDV'
            }
            
            print(f"数据集加载完成: {X.shape[0]} 样本, {X.shape[1]} 特征")
        
        return self.data
    
    def check_memory_usage(self) -> bool:
        """检查内存使用情况"""
        process = psutil.Process(os.getpid())
        memory_mb = process.memory_info().rss / 1024 / 1024
        
        # 定期垃圾回收
        current_time = time.time()
        if current_time - self.last_gc_time > 60:  # 每60秒一次GC
            gc.collect()
            self.last_gc_time = current_time
        
        # 检查是否超过内存限制
        if memory_mb > self.memory_limit_mb:
            print(f"警告：内存使用过高 ({memory_mb:.2f} MB)")
            return False
        
        return True
    
    def create_model(self, model_type: str, hyperparameters: Dict[str, Any]):
        """创建模型实例"""
        try:
            if model_type == 'linear':
                # sklearn>=1.2 中 LinearRegression 不再支持 normalize 参数，这里只保留 fit_intercept
                fit_intercept = hyperparameters.get('fit_intercept', True)
                model = LinearRegression(fit_intercept=fit_intercept)
            elif model_type == 'ridge':
                model = Ridge(alpha=hyperparameters.get('alpha', 1.0))
            elif model_type == 'tree':
                model = DecisionTreeRegressor(
                    max_depth=hyperparameters.get('max_depth', None),
                    min_samples_split=hyperparameters.get('min_samples_split', 2),
                    min_samples_leaf=hyperparameters.get('min_samples_leaf', 1),
                    random_state=MODEL_CONFIG['random_state']
                )
            elif model_type == 'forest':
                model = RandomForestRegressor(
                    n_estimators=hyperparameters.get('n_estimators', 100),
                    max_depth=hyperparameters.get('max_depth', None),
                    min_samples_split=hyperparameters.get('min_samples_split', 2),
                    min_samples_leaf=hyperparameters.get('min_samples_leaf', 1),
                    random_state=MODEL_CONFIG['random_state'],
                    n_jobs=1  # 单线程，避免内存爆炸
                )
            elif model_type == 'gradient':
                model = GradientBoostingRegressor(
                    n_estimators=hyperparameters.get('n_estimators', 100),
                    learning_rate=hyperparameters.get('learning_rate', 0.1),
                    max_depth=hyperparameters.get('max_depth', 3),
                    random_state=MODEL_CONFIG['random_state']
                )
            else:
                raise ValueError(f"未知模型类型: {model_type}")
            
            return model
        except Exception as e:
            print(f"创建模型失败: {e}")
            return None
    
    def train_and_evaluate(self, task: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """训练和评估模型"""
        try:
            # 检查内存使用
            if not self.check_memory_usage():
                return None
            
            # 加载数据
            data = self.load_data()
            X, y = data['X'], data['y']
            
            # 创建模型
            model_type = task.get('model_type', 'tree')
            hyperparameters = task.get('hyperparameters', {})
            
            model = self.create_model(model_type, hyperparameters)
            if model is None:
                return None
            
            # 使用交叉验证评估
            start_time = time.time()
            
            # 使用少量数据快速评估（节省内存）
            if len(X) > 1000:
                X_sample, _, y_sample, _ = train_test_split(
                    X, y, test_size=0.7, random_state=MODEL_CONFIG['random_state']
                )
            else:
                X_sample, y_sample = X, y
            
            # 使用交叉验证一次性计算多种指标
            cv_results = cross_validate(
                model,
                X_sample,
                y_sample,
                cv=min(MODEL_CONFIG['cv_folds'], len(X_sample)),
                scoring={
                    'neg_mse': 'neg_mean_squared_error',
                    'neg_mae': 'neg_mean_absolute_error',
                    'r2': 'r2',
                },
                n_jobs=1,  # 单线程避免内存问题
                return_train_score=False,
            )

            mse_scores = -cv_results['test_neg_mse']
            mae_scores = -cv_results['test_neg_mae']
            r2_scores = cv_results['test_r2']
            rmse_scores = np.sqrt(mse_scores)
            
            training_time = time.time() - start_time
            
            # 收集结果
            result = {
                'task_id': task.get('task_id'),
                'worker_id': self.worker_id,
                'model_type': model_type,
                'hyperparameters': hyperparameters,
                'metrics': {
                    'mean_rmse': float(np.mean(rmse_scores)),
                    'std_rmse': float(np.std(rmse_scores)),
                    'mean_mse': float(np.mean(mse_scores)),
                    'std_mse': float(np.std(mse_scores)),
                    'mean_mae': float(np.mean(mae_scores)),
                    'std_mae': float(np.std(mae_scores)),
                    'mean_r2': float(np.mean(r2_scores)),
                    'std_r2': float(np.std(r2_scores)),
                    'training_time': training_time
                },
                'memory_usage_mb': psutil.Process().memory_info().rss / 1024 / 1024,
                'completed_at': time.time()
            }
            
            # 清理内存
            del model, cv_results, mse_scores, mae_scores, r2_scores, rmse_scores
            gc.collect()
            
            return result
            
        except Exception as e:
            print(f"训练失败: {e}")
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
                    
                    # 训练和评估
                    result = self.train_and_evaluate(task)
                    
                    if result:
                        # 提交结果
                        if self.redis_client.push_result(result):
                            processed_count += 1
                            print(f"工作节点 {self.worker_id} 完成任务: {result['task_id']}, "
                                  f"RMSE: {result['metrics']['mean_rmse']:.4f}")
                        else:
                            print(f"提交结果失败: {result['task_id']}")
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
