## 分布式机器学习任务调度

本项目通过 Redis + 多进程/多机实现分布式训练与超参搜索。对外主入口是命令行工具 ml-ds（Typer 驱动）。

---

## 安装（获取 ml-ds 命令）

1) 准备环境（Python ≥ 3.9）

```bash
cd /home/stu/code/ml-distributed-system （或者在你的目录下）
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

2) 安装项目为本地 CLI

```bash
pip install -e .
ml-ds --help
```

3) 启动 Redis（本机或远程均可）

```bash
# 优先使用项目内配置（如有）
redis-server redis.conf  # 或直接：redis-server
```

---

## 3 分钟上手（用 CLI 跑通）

方式 A：一键本地所有组件

```bash
ml-ds all --tasks 100 -w 2 --monitor
```

方式 B：分步运行（多终端）

```bash
# 1) 下发任务
ml-ds manager --tasks 100

# 2) 启动 worker（单机多进程）
ml-ds worker -c 4

# 3) 收集结果
ml-ds collector --max-results 100
```

运行后，CSV 结果保存在 results/ 下。

---

## 多机/远程 Redis

将所有机器的 CLI 指向同一 Redis 即可水平扩展：

```bash
# 机器A：只负责下发任务
ml-ds manager --redis-url redis://your-redis-host:6379/0 --tasks 1000

# 机器B/C/...：只跑 worker（各自 -c 并发数可不同）
ml-ds worker --redis-url redis://your-redis-host:6379/0 -c 4

# 机器A 或任意一台：收集结果
ml-ds collector --redis-url redis://your-redis-host:6379/0 --max-results 1000
```

如 Redis 开启认证，追加 `--redis-password 'your-password'`。也可用分参覆盖主机、端口、DB：`--redis-host/--redis-port/--redis-db`。

---

## 常用子命令速查

- manager：生成/提交一批任务（自动清空旧队列）

```bash
ml-ds manager --tasks 1000
ml-ds manager --config path/to/conf.json
```

- worker：从队列取任务并训练（本机并发）

```bash
ml-ds worker -c 4
ml-ds worker --redis-url redis://host:6379/0 -c 8
```

- collector：收集结果到 CSV + 打印统计

```bash
ml-ds collector --max-results 1000
ml-ds collector --output-file results_custom.csv
```

- submit-task：提交单个任务

```bash
ml-ds submit-task --model-type tree --hyperparameters '{"max_depth": 5}'
ml-ds submit-task --task-json '{"model_type":"forest","hyperparameters":{"n_estimators":200}}'
```

- all：一机启动 manager + workers + collector（可加 --monitor）

```bash
ml-ds all --tasks 200 -w 4 --monitor
```

完整参数见 CLI 帮助或源码：
- CLI 入口：utils/cli.py
- 打包脚本：pyproject.toml（提供 ml-ds 脚本入口）

---

## 数据文件（自动识别）

把数据放在 data/boston_housing.csv，支持两种格式：
- 带表头 CSV：包含目标列 MEDV；其余为特征。
- 无表头空白分隔：每行 14 个数，最后一列为目标值。

worker 会自动识别，无需改代码。

---

## 故障排查（CLI）

- 无法连接 Redis：确认已启动 `redis-server`；远程用 `--redis-url redis://host:6379/0` 或加 `--redis-host/--redis-port`。
- 端口占用：6379 冲突时，使用 `redis-server --port 6380` 并在 CLI 中同步指定 `--redis-port 6380`。
- 没有结果输出：确认至少一个 `ml-ds worker` 正在运行，且先运行了 `ml-ds manager`。
- 依赖问题：先 `python -m pip install -U pip`；必要时使用国内源 `-i https://pypi.tuna.tsinghua.edu.cn/simple`。

---

## 开发者说明（可选）

开发调试也可直接使用 Python 入口（与 CLI 等价）：
- 入口脚本：run.py（子命令 all/manager/worker/collector/monitor）
- 配置：config/settings.py（Redis、队列名等）
- 组件：manager/、workers/、monitor/、utils/

示例：

```bash
python run.py all --tasks 100 --workers 2 --monitor
```


---

## 附录：CLI 参数速查与示例

参数对照（常用）
- `-w` / `--workers`：`ml-ds all` 模式下本机启动的 worker 进程数（如 `-w 4`）。
- `-c` / `--concurrency`：`ml-ds worker` 的本机并发进程数；`0` 或不填表示按 CPU 核心数自动设置。
- `-t` / `--tasks`：下发的任务数量；用于 `ml-ds manager` 与 `ml-ds all`。
- `-i` / `--interval`：监控刷新间隔（秒）；用于 `ml-ds monitor`。
- `--monitor`：在 `ml-ds all` 中同时启用监控进程。
- `--max-results`：`ml-ds collector` 最多收集的结果条数；为空时持续收集到队列长期为空。
- `--output-file`：`ml-ds collector` 自定义 CSV 文件名（默认带时间戳）。
- `--redis-url`：统一指定 Redis 连接（例如 `redis://host:6379/0`、`redis://:password@host:6379/0`）。
- `--redis-host` / `--redis-port` / `--redis-db` / `--redis-password`：分别覆盖 `--redis-url` 的对应部分。

`redis-url` 原理与限制
- 解析位置：CLI 入口会解析 `--redis-url` 并注入环境变量（`REDIS_HOST/REDIS_PORT/REDIS_DB/REDIS_PASSWORD`）。
- 生效机制：运行时通过配置函数把环境变量转换为连接池（参考 `config/settings.py` 的 `build_redis_config()`）。
- 队列读写：任务以二进制（`pickle + zlib` 压缩）写入/读取；结果以 JSON 字符串写入/读取；`decode_responses=False` 防止 UTF-8 解码错误。
- 当前限制：
	- 支持 `redis://`；接受 `rediss://` 但暂未启用 TLS 参数（若需 TLS 可扩展 `ssl=True` 等配置）。
	- 不处理 URL 查询参数（如 `?timeout=...`）；如需自定义超时需在代码层配置。
	- 不支持 Sentinel/Cluster；目标是直连单实例。
	- URL 中用户名忽略（仅读取密码）；如需 ACL 用户名，需要扩展解析与连接配置。

常见组合与模板
```bash
# 最小演示（本机一键跑通）
ml-ds all --tasks 2

# 本地所有组件（带监控）
ml-ds all --tasks 100 -w 2 --monitor

# 多机：远程 Redis + 并发 worker
ml-ds worker --redis-url redis://your-redis-host:6379/0 -c 8

# 分步：下发任务 + 收集结果
ml-ds manager --redis-url redis://your-redis-host:6379/0 --tasks 1000
ml-ds collector --redis-url redis://your-redis-host:6379/0 --max-results 1000

# 带密码（仅示例）
ml-ds worker --redis-url redis://:your-password@your-redis-host:6379/0 -c 4

# 本机跑满 CPU（让 -c 自动取 CPU 核心数）
ml-ds worker -c 0
```

快速查询与帮助
```bash
ml-ds --help
ml-ds worker --help
ml-ds manager --help
ml-ds collector --help
ml-ds all --help

# 开发者入口帮助（与 CLI 等价）
python run.py -h
```
