import json
import time
import pandas as pd
from typing import Dict, Any, List, Optional
from pathlib import Path
from utils.redis_client import RedisClient
from config.settings import RESULTS_DIR

class ResultCollector:
    """结果收集器（支持增量写入，避免内存溢出，支持按 job_id 过滤统计）"""
    
    def __init__(self, output_file: str = None, job_id: Optional[str] = None):
        self.redis_client = RedisClient()
        self.output_file = output_file or f"results_{int(time.time())}.csv"
        self.output_path = RESULTS_DIR / self.output_file
        self.results_buffer = []
        self.buffer_size = 100  # 每100条结果写入一次文件
        self.total_collected = 0
        self.job_id = job_id
        
        # 初始化结果文件
        self._init_output_file()
    
    def _init_output_file(self):
        """初始化输出文件"""
        if not self.output_path.exists():
            # 创建CSV文件并写入表头
            with open(self.output_path, 'w') as f:
                f.write(
                    "timestamp,job_id,task_id,worker_id,model_type,hyperparameters,"
                    "mean_rmse,std_rmse,mean_mse,std_mse,"
                    "mean_mae,std_mae,mean_r2,std_r2,"
                    "training_time,memory_usage\n"
                )
    
    def _flush_buffer(self):
        """将缓冲区数据写入文件"""
        if not self.results_buffer:
            return
        
        try:
            # 转换为DataFrame并追加到文件
            df = pd.DataFrame(self.results_buffer)
            
            # 格式化数据
            formatted_rows = []
            for _, row in df.iterrows():
                # 将超参数转换为字符串
                hyperparams_str = json.dumps(row['hyperparameters'])
                
                formatted_rows.append({
                    'timestamp': time.strftime('%Y-%m-%d %H:%M:%S', 
                                             time.localtime(row['completed_at'])),
                    'job_id': row.get('job_id'),
                    'task_id': row['task_id'],
                    'worker_id': row['worker_id'],
                    'model_type': row['model_type'],
                    'hyperparameters': hyperparams_str,
                    'mean_rmse': row['mean_rmse'],
                    'std_rmse': row['std_rmse'],
                    'mean_mse': row['mean_mse'],
                    'std_mse': row['std_mse'],
                    'mean_mae': row.get('mean_mae', 0),
                    'std_mae': row.get('std_mae', 0),
                    'mean_r2': row.get('mean_r2', 0),
                    'std_r2': row.get('std_r2', 0),
                    'training_time': row['training_time'],
                    'memory_usage': row['memory_usage']
                })
            
            # 追加到CSV文件
            df_formatted = pd.DataFrame(formatted_rows)
            df_formatted.to_csv(self.output_path, mode='a', 
                              header=False, index=False)
            
            print(f"写入 {len(self.results_buffer)} 条结果到文件，总计 {self.total_collected}")
            self.results_buffer = []
            
        except Exception as e:
            print(f"写入文件失败: {e}")
    
    def process_result(self, result: Dict[str, Any]):
        """处理单个结果"""
        try:
            # 提取关键信息
            processed_result = {
                'job_id': result.get('job_id'),
                'task_id': result.get('task_id'),
                'worker_id': result.get('worker_id'),
                'model_type': result.get('model_type'),
                'hyperparameters': result.get('hyperparameters', {}),
                'completed_at': result.get('completed_at', time.time()),
                'mean_rmse': result.get('metrics', {}).get('mean_rmse', 0),
                'std_rmse': result.get('metrics', {}).get('std_rmse', 0),
                'mean_mse': result.get('metrics', {}).get('mean_mse', 0),
                'std_mse': result.get('metrics', {}).get('std_mse', 0),
                    'mean_mae': result.get('metrics', {}).get('mean_mae', 0),
                    'std_mae': result.get('metrics', {}).get('std_mae', 0),
                    'mean_r2': result.get('metrics', {}).get('mean_r2', 0),
                    'std_r2': result.get('metrics', {}).get('std_r2', 0),
                'training_time': result.get('metrics', {}).get('training_time', 0),
                'memory_usage': result.get('memory_usage_mb', 0)
            }
            
            self.results_buffer.append(processed_result)
            self.total_collected += 1
            
            # 如果缓冲区满了，写入文件
            if len(self.results_buffer) >= self.buffer_size:
                self._flush_buffer()
                
            return True
            
        except Exception as e:
            print(f"处理结果失败: {e}")
            return False
    
    def collect_results(self, max_results: int = None):
        """收集结果"""
        print(f"结果收集器启动，输出文件: {self.output_path}")
        
        collected = 0
        empty_count = 0
        last_progress_time = time.time()
        
        try:
            while True:
                # 获取结果
                result = self.redis_client.pop_result()
                
                if result:
                    self.process_result(result)
                    collected += 1
                    empty_count = 0
                    last_progress_time = time.time()
                    
                    # 显示进度
                    if collected % 10 == 0:
                        print(f"已收集 {collected} 个结果...")
                        
                else:
                    empty_count += 1
                    # 如果连续5次获取不到结果，等待更长时间
                    if empty_count >= 5:
                        print("队列为空，等待新结果...")
                        time.sleep(5)
                        empty_count = 0
                    else:
                        time.sleep(1)
                
                # 检查是否达到最大收集数量
                if max_results and collected >= max_results:
                    print(f"达到最大收集数量 {max_results}，停止收集")
                    break
                
                # 如果长期没有新结果且结果队列已空，认为当前轮次已结束，安全退出
                if not max_results and (time.time() - last_progress_time) > 60:
                    stats = self.redis_client.get_queue_stats()
                    if stats['result_queue_size'] == 0:
                        print("结果队列长时间为空，自动结束收集")
                        break
                
                # 定期刷新缓冲区
                if collected % 50 == 0 and self.results_buffer:
                    self._flush_buffer()
                    
        except KeyboardInterrupt:
            print("\n收到中断信号，停止收集")
        finally:
            # 刷新剩余的结果
            if self.results_buffer:
                self._flush_buffer()
            
            print(f"收集完成！总共收集 {collected} 个结果")
            
            # 生成统计报告
            self.generate_summary()
    
    def generate_summary(self):
        """生成统计报告"""
        try:
            if self.output_path.exists():
                df = pd.read_csv(self.output_path)

                # 如指定了 job_id，则仅统计该 job 下的结果
                if self.job_id and 'job_id' in df.columns:
                    df = df[df['job_id'] == self.job_id]
                
                # 基本统计
                print("\n" + "="*50)
                print("结果统计报告")
                print("="*50)
                print(f"总任务数: {len(df)}")
                print(f"平均RMSE: {df['mean_rmse'].mean():.4f}")
                print(f"最佳RMSE: {df['mean_rmse'].min():.4f}")
                print(f"最差RMSE: {df['mean_rmse'].max():.4f}")
                
                # 按模型类型统计
                if 'model_type' in df.columns:
                    print("\n按模型类型统计:")
                    model_stats = df.groupby('model_type').agg({
                        'mean_rmse': ['mean', 'min', 'count']
                    }).round(4)
                    print(model_stats)
                
                # 找出最佳模型
                if not df.empty:
                    best_idx = df['mean_rmse'].idxmin()
                    best_result = df.loc[best_idx]
                    print(f"\n最佳模型:")
                    print(f"  任务ID: {best_result['task_id']}")
                    print(f"  模型类型: {best_result['model_type']}")
                    print(f"  RMSE: {best_result['mean_rmse']:.4f}")
                    print(f"  超参数: {best_result['hyperparameters']}")
                
        except Exception as e:
            print(f"生成统计报告失败: {e}")
