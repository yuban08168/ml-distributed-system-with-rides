import json
import multiprocessing as mp
import os
import time
import uuid
from typing import Optional
from urllib.parse import urlparse

import typer

from manager.task_manager import TaskManager
from manager.result_collector import ResultCollector
from monitor.monitor import SystemMonitor
from utils.redis_client import RedisClient
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


@app.command("manager")
def manager_cmd(
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
    job_id: Optional[str] = typer.Option(
        None,
        help="本次任务批次的 job_id，不填则自动生成",
    ),
    tasks: int = typer.Option(
        1000,
        "--tasks",
        "-t",
        help="要生成并提交的任务数量",
    ),
    config: Optional[str] = typer.Option(
        None,
        help="任务配置文件路径（JSON），可指定 job_id / total_tasks / model_types 等",
    ),
) -> None:
    """启动任务管理器，生成一批任务并提交到 Redis 队列。"""

    _configure_redis_from_args(
        redis_url=redis_url,
        redis_host=redis_host,
        redis_port=redis_port,
        redis_db=redis_db,
        redis_password=redis_password,
    )

    # 解析配置文件（如提供）
    total_tasks = tasks
    model_types = None

    if config:
        try:
            with open(config, "r", encoding="utf-8") as f:
                conf = json.load(f)
        except Exception as exc:
            raise typer.BadParameter(f"无法读取配置文件 {config}: {exc}")

        if isinstance(conf, dict):
            total_tasks = int(conf.get("total_tasks", total_tasks))
            if not job_id:
                job_id = conf.get("job_id")
            mt = conf.get("model_types")
            if isinstance(mt, list):
                model_types = [str(m) for m in mt]

    # 最终 job_id 决定
    job_id = job_id or f"job_{int(time.time())}"

    typer.echo(f"启动任务管理器，job_id={job_id}，准备生成 {total_tasks} 个任务...")

    # 每次启动前清空任务/结果队列，避免读到旧数据
    redis_client = RedisClient()
    redis_client.reset_queues()

    manager = TaskManager(job_id=job_id, model_types=model_types)
    success, failed = manager.submit_tasks_parallel(total_tasks=total_tasks)

    typer.echo(f"任务提交完成，总计 {tasks} 个，成功: {success}，失败: {failed}")
    if failed > 0:
        raise typer.Exit(code=1)


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
    job_id: Optional[str] = typer.Option(
        None,
        help="本次单个任务所属的 job_id，不填则自动生成",
    ),
    task_file: Optional[str] = typer.Option(
        None,
        help="单个任务配置文件（JSON），内容为一个任务字典；如提供则优先使用",
    ),
    task_json: Optional[str] = typer.Option(
        None,
        help="完整任务 JSON 字符串，若提供则优先使用；示例: '{\"model_type\": \"tree\", \"hyperparameters\": {\"max_depth\": 5}}'",
    ),
    model_type: Optional[str] = typer.Option(
        None,
        help="模型类型，例如 linear/tree/forest/gradient；当未提供 task-json 时必填",
    ),
    hyperparameters: Optional[str] = typer.Option(
        None,
        help="超参数 JSON 字符串，当未提供 task-json 时必填；例如 '{\"max_depth\": 5, \"n_estimators\": 100}'",
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

    if task_file is not None:
        try:
            with open(task_file, "r", encoding="utf-8") as f:
                task = json.load(f)
            if not isinstance(task, dict):
                raise ValueError
        except Exception:
            raise typer.BadParameter("task-file 必须是包含单个任务对象的 JSON 文件")
        # 补充必要字段
        task.setdefault("task_id", f"cli_{uuid.uuid4().hex}")
        task.setdefault("created_at", time.time())
        task.setdefault("status", "pending")
        task.setdefault("retry_count", 0)
        task.setdefault("job_id", job_id or f"job_{int(time.time())}")

    elif task_json is None:
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
            "job_id": job_id or f"job_{int(time.time())}",
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
        task.setdefault("job_id", job_id or f"job_{int(time.time())}")

    manager = TaskManager()
    success, failed = manager.submit_tasks_batch([task])

    typer.echo(f"任务提交完成，成功: {success}，失败: {failed}")
    if failed > 0:
        raise typer.Exit(code=1)


@app.command("collector")
def collector_cmd(
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
    max_results: Optional[int] = typer.Option(
        None,
        help="最大收集结果数；为空时会持续收集直到结果队列长时间为空",
    ),
    output_file: Optional[str] = typer.Option(
        None,
        help="结果输出文件名（位于 results 目录下），默认包含时间戳",
    ),
    job_id: Optional[str] = typer.Option(
        None,
        help="仅对指定 job_id 的结果进行统计（CSV 仍会包含所有 job 的行）",
    ),
) -> None:
    """启动结果收集器，从结果队列拉取结果、写入 CSV 并生成统计报告。"""

    _configure_redis_from_args(
        redis_url=redis_url,
        redis_host=redis_host,
        redis_port=redis_port,
        redis_db=redis_db,
        redis_password=redis_password,
    )

    collector = ResultCollector(output_file=output_file, job_id=job_id)
    collector.collect_results(max_results=max_results)


@app.command("monitor")
def monitor_cmd(
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
    interval: int = typer.Option(
        10,
        "--interval",
        "-i",
        help="监控间隔（秒）",
    ),
) -> None:
    """启动系统和队列监控。"""

    _configure_redis_from_args(
        redis_url=redis_url,
        redis_host=redis_host,
        redis_port=redis_port,
        redis_db=redis_db,
        redis_password=redis_password,
    )

    monitor = SystemMonitor()
    monitor.monitor(interval=interval)


@app.command("all")
def all_cmd(
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
    tasks: int = typer.Option(
        100,
        "--tasks",
        "-t",
        help="要生成并提交的任务数量",
    ),
    workers: int = typer.Option(
        2,
        "--workers",
        "-w",
        help="本机并发 worker 进程数",
    ),
    enable_monitor: bool = typer.Option(
        False,
        "--monitor",
        help="是否同时启动监控进程",
    ),
    job_id: Optional[str] = typer.Option(
        None,
        help="本次 all 运行的 job_id，不填则自动生成",
    ),
) -> None:
    """在本机一键启动 manager + 多个 worker + collector (+ 可选监控)。"""

    _configure_redis_from_args(
        redis_url=redis_url,
        redis_host=redis_host,
        redis_port=redis_port,
        redis_db=redis_db,
        redis_password=redis_password,
    )

    job_id = job_id or f"job_{int(time.time())}"
    typer.echo(f"启动本地 all 模式: job_id={job_id}, manager + workers + collector")

    # 每次 all 模式运行前清空任务/结果队列
    redis_client = RedisClient()
    redis_client.reset_queues()

    processes: list[mp.Process] = []

    def _manager_proc(total_tasks: int, the_job_id: str) -> None:
        mgr = TaskManager(job_id=the_job_id)
        mgr.submit_tasks_parallel(total_tasks=total_tasks)

    def _collector_proc(max_results: int, the_job_id: str) -> None:
        collector = ResultCollector(job_id=the_job_id)
        collector.collect_results(max_results=max_results)

    def _monitor_proc(interval_sec: int) -> None:
        monitor = SystemMonitor()
        monitor.monitor(interval=interval_sec)

    # 1. manager
    manager_p = mp.Process(target=_manager_proc, args=(tasks, job_id), name="ml-manager")
    manager_p.start()
    processes.append(manager_p)

    # 2. workers
    for i in range(workers):
        p = mp.Process(target=_worker_process_main, name=f"ml-worker-{i}")
        p.start()
        processes.append(p)

    # 3. collector
    collector_p = mp.Process(target=_collector_proc, args=(tasks, job_id), name="ml-collector")
    collector_p.start()
    processes.append(collector_p)

    # 4. monitor (optional)
    if enable_monitor:
        monitor_p = mp.Process(target=_monitor_proc, args=(10,), name="ml-monitor")
        monitor_p.start()
        processes.append(monitor_p)

    typer.echo(
        f"所有组件已启动：tasks={tasks}, workers={workers}, monitor={'启用' if enable_monitor else '未启用'}"
    )

    try:
        for p in processes:
            p.join()
    except KeyboardInterrupt:
        typer.echo("收到中断信号，正在停止所有组件...")
        for p in processes:
            if p.is_alive():
                p.terminate()
        for p in processes:
            p.join()


if __name__ == "__main__":  # pragma: no cover
    app()
