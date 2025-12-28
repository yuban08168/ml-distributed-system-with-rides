import os
from pathlib import Path


def build_redis_config() -> dict:
    """根据当前环境变量构建 Redis 配置。

    提供函数形式便于在运行时（例如通过 CLI 设置环境变量后）
    动态获取最新配置，而不是在导入时就固定死。
    """

    return {
        'host': os.getenv('REDIS_HOST', 'localhost'),  # 从环境变量读取，失败则默认localhost
        'port': int(os.getenv('REDIS_PORT', 6379)),
        'password': os.getenv('REDIS_PASSWORD', None),  # 无密码时默认为None
        # 任务队列中存放的是压缩后的二进制数据，必须关闭自动字符串解码
        'db': int(os.getenv('REDIS_DB', 0)),
        'decode_responses': False,
        'socket_timeout': 30,
        'socket_connect_timeout': 30,
        'retry_on_timeout': True,
        'max_connections': 20,
    }


# 保留一个静态变量以兼容旧代码，但推荐在新代码中使用 build_redis_config()
REDIS_CONFIG = build_redis_config()

# 队列配置
QUEUE_NAMES = {
    'task_queue': 'ml:tasks:queue',
    'result_queue': 'ml:results:queue',
    'task_progress': 'ml:tasks:progress',
    'worker_status': 'ml:workers:status'
}

# 任务配置
TASK_CONFIG = {
    'max_retries': 3,
    'timeout': 3600,  # 任务超时时间（秒）
    'batch_size': 100,  # 批量处理大小
    'max_queue_size': 10000  # 最大队列长度
}

# 路径配置
BASE_DIR = Path(__file__).parent.parent
DATA_DIR = BASE_DIR / 'data'
RESULTS_DIR = BASE_DIR / 'results'
LOG_DIR = BASE_DIR / 'logs'

# 创建必要的目录
for dir_path in [DATA_DIR, RESULTS_DIR, LOG_DIR]:
    dir_path.mkdir(exist_ok=True)

# 模型配置
MODEL_CONFIG = {
    'cv_folds': 5,
    'random_state': 42,
    'test_size': 0.2
}
