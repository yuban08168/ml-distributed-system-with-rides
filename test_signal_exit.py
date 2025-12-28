#!/usr/bin/env python3
"""\
简单脚本：验证 worker 在训练中途收到退出信号时，当前计算是否丢失。

流程：
1. 运行 manager 下发固定数量任务（默认 10 个），会自动重置队列。
2. 启动 worker-1，等待一段时间后发送 SIGTERM（模拟中途退出）。
3. 启动 worker-2，处理剩余任务并自动退出。
4. 启动 collector 收集结果。
5. 统计 results 目录中最新的结果文件行数（减去表头），验证是否等于任务数。

使用方法：
    cd /home/stu/code/ml-distributed-system
    source distributed/bin/activate  # 或你的 venv
    python test_signal_exit.py --tasks 15 --delay 8

"""

import argparse
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

PROJECT_ROOT = Path(__file__).parent.resolve()
RESULTS_DIR = PROJECT_ROOT / "results"


def run_manager(tasks: int) -> None:
    print(f"[STEP] 启动 manager，下发 {tasks} 个任务……")
    cmd = [sys.executable, "run.py", "manager", "--tasks", str(tasks)]
    subprocess.run(cmd, cwd=PROJECT_ROOT, check=True)
    print("[OK] manager 运行结束，任务已写入队列。\n")


def start_worker(worker_id: str) -> subprocess.Popen:
    print(f"[STEP] 启动 worker：{worker_id}")
    cmd = [sys.executable, "run.py", "worker", "--worker-id", worker_id]
    proc = subprocess.Popen(cmd, cwd=PROJECT_ROOT)
    print(f"[INFO] worker {worker_id} PID = {proc.pid}")
    return proc


def stop_worker_gracefully(proc: subprocess.Popen, sig: int = signal.SIGTERM, timeout: float = 60.0) -> None:
    if proc.poll() is not None:
        print("[WARN] worker 已经退出，无需再发送信号。")
        return

    print(f"[STEP] 向 worker(PID={proc.pid}) 发送信号 {sig} ……")
    try:
        os.kill(proc.pid, sig)
    except ProcessLookupError:
        print("[WARN] worker 进程不存在，可能已经退出。")
        return

    try:
        proc.wait(timeout=timeout)
        print("[OK] worker 已优雅退出。\n")
    except subprocess.TimeoutExpired:
        print("[WARN] worker 在超时时间内未退出，将尝试强制终止。")
        proc.kill()


def run_collector(max_results: int) -> None:
    print(f"[STEP] 启动 collector，最多收集 {max_results} 条结果……")
    cmd = [sys.executable, "run.py", "collector", "--max-results", str(max_results)]
    subprocess.run(cmd, cwd=PROJECT_ROOT, check=True)
    print("[OK] collector 运行结束。\n")


def _get_latest_result_file() -> Optional[Path]:
    if not RESULTS_DIR.exists():
        return None
    candidates = list(RESULTS_DIR.glob("results_*.csv"))
    if not candidates:
        return None
    return max(candidates, key=lambda p: p.stat().st_mtime)


def count_results_rows(path: Path) -> int:
    """统计结果文件中数据行数（不含表头）。"""
    import csv

    with path.open("r", encoding="utf-8") as f:
        reader = csv.reader(f)
        rows = list(reader)
    if not rows:
        return 0
    # 默认第一行是表头
    return max(0, len(rows) - 1)


def main() -> None:
    parser = argparse.ArgumentParser(description="测试 worker 中途退出是否丢失计算的小脚本")
    parser.add_argument("--tasks", type=int, default=10, help="测试任务数量")
    parser.add_argument("--delay", type=float, default=8.0, help="给第一个 worker 发送退出信号前的等待秒数")
    args = parser.parse_args()

    tasks = args.tasks
    delay = args.delay

    print("=== 信号退出安全性测试开始 ===")
    print(f"总任务数: {tasks}, 中途发送信号前延迟: {delay} 秒")
    print("项目根目录:", PROJECT_ROOT)
    print()

    # 1. 运行 manager，下发任务
    run_manager(tasks)

    # 2. 启动第一个 worker，等待一段时间后发送 SIGTERM
    worker1 = start_worker("signal-test-worker-1")
    print(f"[INFO] 等待 {delay} 秒，让 worker1 进入训练过程……")
    time.sleep(delay)
    stop_worker_gracefully(worker1, sig=signal.SIGTERM)

    # 3. 启动第二个 worker，处理剩余任务并自动退出
    print("[STEP] 启动第二个 worker 处理剩余任务……")
    worker2 = start_worker("signal-test-worker-2")
    # 让 worker2 自然跑到任务队列为空并自动退出
    worker2.wait()
    print("[OK] 第二个 worker 已退出。\n")

    # 4. 启动 collector 收集结果
    run_collector(tasks)

    # 5. 查找最新的结果文件并统计行数
    latest = _get_latest_result_file()
    if latest is None:
        print("[FAIL] 未找到任何 results_*.csv 结果文件，可能 collector 没有写入结果。")
        sys.exit(1)

    row_count = count_results_rows(latest)
    print(f"[INFO] 最新结果文件: {latest}")
    print(f"[INFO] 结果文件中数据行数(不含表头): {row_count}")

    if row_count >= tasks:
        print("[PASS] 结果条数 >= 任务数，说明在中途发送 SIGTERM 时，当前任务的计算结果没有丢失。")
    else:
        print("[WARN] 结果条数 < 任务数，存在任务结果丢失的可能，请检查 worker 日志和 Redis 队列。")

    print("=== 测试结束 ===")


if __name__ == "__main__":
    main()
