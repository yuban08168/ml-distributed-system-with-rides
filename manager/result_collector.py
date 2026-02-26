import json
import time
from typing import Dict, Any, Optional, List
from pathlib import Path

import pandas as pd

from utils.redis_client import RedisClient
from config.settings import RESULTS_DIR


class ResultCollector:
    """通用结果收集器。

    不再假设结果结构中一定存在特定的机器学习指标字段，而是：
    - 将从 Redis 结果队列中取出的字典原样缓存在内存中；
    - 定期使用 pandas.json_normalize 动态展开为表格并追加到 CSV；
    - 至少保证 job_id / task_id / worker_id / completed_at 等通用字段
      若存在会被一起写入，其他业务字段完全由用户自定义。
    """

    def __init__(self, output_file: str = None, job_id: Optional[str] = None):
        self.redis_client = RedisClient()
        self.output_file = output_file or f"results_{int(time.time())}.csv"
        self.output_path = RESULTS_DIR / self.output_file
        self.results_buffer: List[Dict[str, Any]] = []
        self.buffer_size = 100  # 每100条结果写入一次文件
        self.total_collected = 0
        self.job_id = job_id

        # 初始化输出文件（首批写入时再创建表头）
        self._init_output_file()

    def _init_output_file(self) -> None:
        """初始化输出文件（如不存在则创建空文件）。"""
        if not self.output_path.exists():
            # 创建空文件，占位用；首批 flush 时会写入表头
            self.output_path.touch()

    def _flush_buffer(self) -> None:
        """将缓冲区数据写入文件（CSV，动态列）。"""
        if not self.results_buffer:
            return

        try:
            # 可选按 job_id 过滤
            data = self.results_buffer
            if self.job_id is not None:
                data = [r for r in data if r.get("job_id") == self.job_id]

            if not data:
                self.results_buffer = []
                return

            # 使用 json_normalize 展开任意嵌套结构
            df = pd.json_normalize(data)

            # 若存在 completed_at 字段，可派生一个可读时间戳列
            if "completed_at" in df.columns:
                df["timestamp"] = df["completed_at"].apply(
                    lambda ts: time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts))
                    if pd.notnull(ts)
                    else ""
                )

            # 是否需要写表头
            write_header = not self.output_path.exists() or self.output_path.stat().st_size == 0

            df.to_csv(
                self.output_path,
                mode="a",
                header=write_header,
                index=False,
            )

            print(f"写入 {len(data)} 条结果到文件，总计 {self.total_collected}")
            self.results_buffer = []

        except Exception as e:
            print(f"写入文件失败: {e}")

    def process_result(self, result: Dict[str, Any]) -> bool:
        """处理单个结果（直接缓冲原始字典）。"""
        try:
            if not isinstance(result, dict):
                raise TypeError("结果必须是字典类型")

            self.results_buffer.append(result)
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
            
            # 统计报告属于业务层，调度层不再内置特定指标分析逻辑

