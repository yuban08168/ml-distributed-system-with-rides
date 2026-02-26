"""任务执行接口占位实现。

使用者应在自己的项目中替换或扩展此模块，实现 execute_task 函数，
以便 worker 进程通过调度系统分发任务并执行实际计算逻辑。

推荐做法：
- 保持函数签名不变：execute_task(task: dict) -> dict
- 自己的代码可以通过 Git 拉取到同一环境，或打包进 Docker 镜像中，
  只要该模块在 Python 路径上可见即可。
"""
from typing import Dict, Any


def execute_task(task: Dict[str, Any]) -> Dict[str, Any]:
    """用户需要实现的任务执行函数。

    参数
    ------
    task: dict
        从 Redis 任务队列中取出的任务字典，结构由用户自行约定。

    返回
    ------
    result: dict
        需要可被 JSON 序列化的结果字典。调度系统会在外围补充
        task_id / job_id / worker_id 等通用字段，并推送到结果队列。

    默认实现仅作为占位，会直接抛出异常，提示用户自行实现。
    """
    raise NotImplementedError(
        "请在 workers/executor.py 中实现 execute_task，"
        "或通过自定义模块提供同名函数供 worker 调用。"
    )
