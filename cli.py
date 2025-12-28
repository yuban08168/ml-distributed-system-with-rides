import json
import multiprocessing as mp
import os
import time
import uuid
from typing import Optional
from urllib.parse import urlparse

import typer

from manager.task_manager import TaskManager
from workers.worker import MLWorker


app = typer.Typer(help="ML 分布式系统命令行工具")


def _configure_redis_from_args(
    redis_url: Optional[str],
    redis_host: Optional[str],
    redis_port: Optional[int],
    redis_db: Optional[int],
    redis_password: Optional[str],
) -> None:
    """根据命令行参数配置 Redis 环境变量，供 RedisClient 使用。

    现有 RedisClient 依赖 config.settings.REDIS_CONFIG，而 REDIS_CONFIG
    又是从环境变量读取的，这里通过设置环境变量的方式来注入配置，
    避免大改现有代码结构。
    """

    # 优先解析 redis_url
    if redis_url:
        parsed = urlparse(redis_url)
        if parsed.scheme not in {"redis", "rediss"}:
            raise typer.BadParameter("redis-url 必须以 redis:// 或 rediss:// 开头")

        if parsed.hostname:
            os.environ["REDIS_HOST"] = parsed.hostname
        if parsed.port is not None:
            os.environ["REDIS_PORT"] = str(parsed.port)
        if parsed.path and parsed.path != "/":
            # path 形如 /0
            try:
                db_index = int(parsed.path.lstrip("/"))
                os.environ["REDIS_DB"] = str(db_index)
            except ValueError:
                raise typer.BadParameter("redis-url 中的 DB 必须是整数，例如 redis://host:6379/0")
        if parsed.password:
            os.environ["REDIS_PASSWORD"] = parsed.password

    # 显式参数覆盖 URL / 默认值
    if redis_host:
        os.environ["REDIS_HOST"] = redis_host
    if redis_port is not None:
        os.environ["REDIS_PORT"] = str(redis_port)
    if redis_db is not None:
        os.environ["REDIS_DB"] = str(redis_db)
    if redis_password is not None:
        os.environ["REDIS_PASSWORD"] = redis_password


def _worker_process_main() -> None:
    """子进程入口：启动一个 MLWorker 并阻塞运行。"""

    worker = MLWorker()
    worker.run()


@app.command()
def worker(
    redis_url: Optional[str] = typer.Option(
        None,
        help="Redis 连接地址，例如 redis://localhost:6379/0",
    ),
    redis_host: Optional[str] = typer.Option(
        None,
        help="Redis 主机名，若同时提供 redis-url，则此参数会覆盖其中的 host",
    ),
    redis_port: Optional[int] = typer.Option(
        None,
        help="Redis 端口，若同时提供 redis-url，则此参数会覆盖其中的 port",
    ),
    redis_db: Optional[int] = typer.Option(
        None,
        help="Redis DB 索引，若同时提供 redis-url，则此参数会覆盖其中的 db",
    ),
    redis_password: Optional[str] = typer.Option(
        None,
        help="Redis 密码，若不需要认证可忽略",
    ),
    concurrency: int = typer.Option(
        0,
        "--concurrency",
        "-c",
        help="并发 worker 进程数，0 或不填写表示自动按 CPU 核心数",
    ),
) -> None:
    """启动本机 worker，自动从 Redis 队列拉取任务并执行。"""

    _configure_redis_from_args(
        redis_url=redis_url,
        redis_host=redis_host,
        redis_port=redis_port,
        redis_db=redis_db,
        redis_password=redis_password,
    )

    if concurrency <= 0:
        concurrency = mp.cpu_count()

    typer.echo(f"使用 {concurrency} 个本地进程作为 worker")

    processes = []
    for i in range(concurrency):
        p = mp.Process(target=_worker_process_main, name=f"ml-worker-{i}")
        p.start()
        processes.append(p)

    # 等待所有子进程退出
    try:
        for p in processes:
            p.join()
    except KeyboardInterrupt:
        typer.echo("收到中断信号，正在停止所有 worker...")
        for p in processes:
            p.terminate()
        for p in processes:
            p.join()


@app.command("submit-task")
def submit_task(
    redis_url: Optional[str] = typer.Option(
        None,
        help="Redis 连接地址，例如 redis://localhost:6379/0",
    ),
    redis_host: Optional[str] = typer.Option(
        None,
        help="Redis 主机名，若同时提供 redis-url，则此参数会覆盖其中的 host",
    ),
    redis_port: Optional[int] = typer.Option(
        None,
        help="Redis 端口，若同时提供 redis-url，则此参数会覆盖其中的 port",
    ),
    redis_db: Optional[int] = typer.Option(
        None,
        help="Redis DB 索引，若同时提供 redis-url，则此参数会覆盖其中的 db",
    ),
    redis_password: Optional[str] = typer.Option(
        None,
        help="Redis 密码，若不需要认证可忽略",
    ),
    task_json: Optional[str] = typer.Option(
        None,
        help="完整任务 JSON 字符串，若提供则优先使用；示例: '{"model_type": "tree", "hyperparameters": {"max_depth": 5}}'",
    ),
    model_type: Optional[str] = typer.Option(
        None,
        help="模型类型，例如 linear/tree/forest/gradient；当未提供 task-json 时必填",
    ),
    hyperparameters: Optional[str] = typer.Option(
        None,
        help="超参数 JSON 字符串，当未提供 task-json 时必填；例如 '{"max_depth": 5, "n_estimators": 100}'",
    ),
) -> None:
    """向 Redis 中提交单个任务，供 worker 拉取执行。"""

    _configure_redis_from_args(
        redis_url=redis_url,
        redis_host=redis_host,
        redis_port=redis_port,
        redis_db=redis_db,
        redis_password=redis_password,
    )

    if task_json is None:
        # 走结构化参数模式
        if not model_type or not hyperparameters:
            raise typer.BadParameter(
                "当未提供 --task-json 时，必须同时提供 --model-type 和 --hyperparameters"
            )
        try:
            hyper_dict = json.loads(hyperparameters)
            if not isinstance(hyper_dict, dict):
                raise ValueError
        except Exception:
            raise typer.BadParameter("hyperparameters 必须是合法的 JSON 对象字符串")

        task = {
            "task_id": f"cli_{uuid.uuid4().hex}",
            "model_type": model_type,
            "hyperparameters": hyper_dict,
            "created_at": time.time(),
            "status": "pending",
            "retry_count": 0,
        }
    else:
        # 直接使用用户提供的 task-json
        try:
            task = json.loads(task_json)
            if not isinstance(task, dict):
                raise ValueError
        except Exception:
            raise typer.BadParameter("task-json 必须是合法的 JSON 对象字符串")

        # 补充必要字段
        task.setdefault("task_id", f"cli_{uuid.uuid4().hex}")
        task.setdefault("created_at", time.time())
        task.setdefault("status", "pending")
        task.setdefault("retry_count", 0)

    manager = TaskManager()
    success, failed = manager.submit_tasks_batch([task])

    typer.echo(f"任务提交完成，成功: {success}，失败: {failed}")
    if failed > 0:
        raise typer.Exit(code=1)


if __name__ == "__main__":  # pragma: no cover
    app()
