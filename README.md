## 分布式任务调度框架（计算逻辑可插拔）

本项目提供一个基于 Redis + 多进程/多机的**通用分布式任务调度系统**。

- 调度层负责：任务下发、队列管理、worker 并发执行、结果回收、系统监控。
- 计算层（真正干活的代码）通过一个可插拔的 `execute_task(task: dict) -> dict`
	接口完全由使用者自己实现。

对外主入口仍然是命令行工具 ml-ds（Typer 驱动），但框架本身不再内置具体
“训练模型 + 超参搜索”逻辑，只保留调度能力。

---

## 安装（获取 ml-ds 命令）

1) 准备环境（Python ≥ 3.9）
	- 建议使用 Python 3.9 或更高版本。
	- 可选：创建并激活虚拟环境（`python -m venv .venv && source .venv/bin/activate`）。

2) 获取代码
	- 方式一：从 Git 克隆本仓库。
	- 方式二：下载压缩包并解压。

3) 安装依赖与命令行入口
	- 在项目根目录执行：

	  ```bash
	  python -m pip install -U pip
	  python -m pip install -r requirements.txt
	  # 开发/本地运行（推荐）：
	  python -m pip install -e .
	  ```

4) 验证安装
	- 终端中执行：

	  ```bash
	  ml-ds --help
	  ```

	如能看到子命令帮助，即说明安装成功。

## 更新日志

### 2026-02-26

- 抽象计算逻辑：
	- 将原本内置在 worker 中的机器学习训练/评估逻辑完全抽离，保留统一的调度壳。
	- 新增通用执行接口 `execute_task(task: dict) -> dict`，默认占位实现位于 [workers/executor.py](workers/executor.py)。
	- worker 启动时通过环境变量 `MLDS_EXECUTOR` 动态加载实际执行函数（形如 `my_project.executor:execute_task`），实现“计算逻辑可插拔”。

- 结果收集器泛化：
	- 重写 [manager/result_collector.py](manager/result_collector.py)，不再依赖 `mean_rmse` 等固定 ML 指标字段。
	- 结果以任意字典形式从 Redis 读取，使用 `pandas.json_normalize` 动态展开为表格列，统一写入 CSV。
	- 仍保留 `task_id/job_id/worker_id/completed_at` 等通用字段，但业务字段结构完全由执行器返回结果决定。

- 内置 sklearn 执行器（零代码跑 ML）：
	- 新增 [workers/ml_sklearn_executor.py](workers/ml_sklearn_executor.py)，提供开箱即用的 “ML 训练 + 交叉验证 + 指标输出” 实现。
	- 支持通过现有的 TaskManager 生成任务（`model_type` + `hyperparameters`），对不同模型/超参组合进行分布式搜索。
	- 通过环境变量控制数据与目标列：
		- `MLDS_DATA_CSV`: 数据集 CSV 路径，默认 `data/boston_housing.csv`。
		- `MLDS_TARGET_COL`: 目标列名，默认 `MEDV`。
	- 自动处理泛型数据集：
		- 若目标列为非数值类型（如字符串标签），会自动做整数编码。
		- 特征端仅保留数值型列，自动丢弃字符串/类别型列，避免 sklearn "could not convert string to float" 错误。

- README 与使用文档：
	- README 重写为“通用分布式任务调度框架”视角，强调调度层/计算层解耦与可插拔执行器模式。
	- 新增内置 sklearn 执行器与相关环境变量（`MLDS_EXECUTOR`/`MLDS_DATA_CSV`/`MLDS_TARGET_COL`）的使用示例，覆盖单机与分布式场景。

---

## 使用说明

本节集中说明如何在不同场景下使用本框架，包括单机快速上手、多机部署、
自定义/内置执行器的使用方式以及常用 CLI 命令与参数。

### 0. 运行前准备（必须）

下面所有示例都假设你已经完成以下准备步骤：

1) 已按前文“安装（获取 ml-ds 命令）”章节完成安装，并能正常执行 `ml-ds --help`。

2) 启动本机 Redis 服务（开启一个新终端执行）：

```bash
cd /home/stu/code/ml-distributed-system  # 或你的项目路径
redis-server
```

保持该终端不关闭，后续所有 `ml-ds ...` 命令都会连接到这个 Redis。

3) 为了能“开箱即用”直接看到效果，推荐先使用内置 sklearn 执行器：

```bash
# 在运行 worker / all 之前执行（如在你的主终端里）
export MLDS_EXECUTOR="workers.ml_sklearn_executor:execute_task"

# 可选：指定数据集和目标列（不设置则使用默认的波士顿房价示例）
export MLDS_DATA_CSV="data/boston_housing.csv"
export MLDS_TARGET_COL="MEDV"
```

完成以上 3 步后，你可以直接从“1. 本机快速上手”开始按顺序运行下面的命令，
即可在 results/ 目录看到生成的结果 CSV 文件。

### 1. 本机快速上手

**方式 A：一键本地所有组件（推荐初次体验）**

```bash
ml-ds all --tasks 100 -w 2 --monitor
```

- `--tasks 100`：随机生成并下发 100 个任务。
- `-w 2`：本机启动 2 个 worker 进程并发执行。
- `--monitor`：额外启动监控进程，定期打印队列与系统资源使用情况。

**方式 B：分步运行（多终端）**

```bash
# 1) 下发任务
ml-ds manager --tasks 100

# 2) 启动 worker（单机多进程）
ml-ds worker -c 4

# 3) 收集结果
ml-ds collector --max-results 100
```

运行后，worker 会调用当前配置的 `execute_task`，collector 会把每条结果
展开成一行追加到 results/ 目录下的 CSV 文件中。

> 注意：如果你没有实现 `workers/executor.py` 中的 `execute_task`，
> 且未设置 `MLDS_EXECUTOR` 指向其它实现，worker 会抛出 NotImplementedError。

### 2. 多机 / 远程 Redis 部署

将所有机器的 CLI 指向同一 Redis 即可水平扩展 worker 数量：

```bash
# 机器 A：只负责下发任务
ml-ds manager --redis-url redis://your-redis-host:6379/0 --tasks 1000

# 机器 B/C/...：只跑 worker（各自 -c 并发数可不同）
ml-ds worker --redis-url redis://your-redis-host:6379/0 -c 4

# 机器 A 或任意一台：收集结果
ml-ds collector --redis-url redis://your-redis-host:6379/0 --max-results 1000
```

- 如 Redis 开启认证，可追加：`--redis-password 'your-password'`。
- 也可用拆分参数覆盖主机、端口、DB：`--redis-host/--redis-port/--redis-db`。

### 3. 接入你自己的计算逻辑

调度系统只关心：
- 任务：任何可被 pickle 序列化的字典；
- 结果：任何可被 JSON 序列化的字典。

#### 3.1 在仓库内实现执行函数

在 [workers/executor.py](workers/executor.py) 中默认提供占位实现：

```python
def execute_task(task: Dict[str, Any]) -> Dict[str, Any]:
	raise NotImplementedError("请在 workers/executor.py 中实现 execute_task")
```

你可以将其改为自己的业务逻辑，例如：

```python
def execute_task(task: Dict[str, Any]) -> Dict[str, Any]:
	input_data = task.get("input")
	# ... 执行实际计算 ...
	output = run_my_model(input_data)

	return {
		"status": "ok",
		"output": output,
	}
```

worker 会在结果外层自动补充 `task_id/job_id/worker_id/completed_at` 等通用字段。

#### 3.2 使用自定义模块作为执行器

如果你的执行逻辑在独立仓库或包中，可通过环境变量指定执行器：

```bash
export MLDS_EXECUTOR="my_project.executor:execute_task"
ml-ds worker -c 4
```

- `my_project.executor`：Python 模块名；
- `execute_task`：该模块中暴露的函数名。

只要该模块在 Python 路径上（例如通过 Git 拉取到同一环境、
或打包进 Docker 镜像中并通过 `PYTHONPATH` 暴露），worker 就能找到它。

### 4. 不写代码：使用内置 sklearn 执行器

如果你只是想跑一个基于 tabular 数据的 sklearn 模型超参搜索，
可以直接使用内置执行器 [workers/ml_sklearn_executor.py](workers/ml_sklearn_executor.py)：

- 模块路径：`workers.ml_sklearn_executor:execute_task`
- 任务结构：沿用 TaskManager 生成的 `model_type` + `hyperparameters` 字段。

典型使用流程：

```bash
# 1) 指定使用内置 sklearn 执行器
export MLDS_EXECUTOR="workers.ml_sklearn_executor:execute_task"

# 2) 指定数据集和目标列（可选，默认使用 data/boston_housing.csv + MEDV）
export MLDS_DATA_CSV="data/boston_housing.csv"   # 或任意你的 CSV 路径
export MLDS_TARGET_COL="MEDV"                    # 你的目标列名

# 3) 下发一批默认 ML 任务
ml-ds manager --tasks 200

# 4) 启动 worker 并收集结果
ml-ds worker -c 4
ml-ds collector --max-results 200
```

特性：
- 自动对非数值目标列做整数编码（适用于分类标签场景，如 `READMITTED`）。
- 特征端仅保留数值型列，自动丢弃字符串/类别型列，避免 sklearn 报
  “could not convert string to float” 错误。

因此你可以通过设置：

```bash
export MLDS_DATA_CSV="data/DIABETIC_DATA.csv"
export MLDS_TARGET_COL="READMITTED"
```

直接在 DIABETIC_DATA 这类包含大量离散特征的数据集上验证完整流程。

### 5. 常用 CLI 子命令与参数

**子命令概览**

- `manager`：生成/提交一批任务（自动清空旧队列）。
- `worker`：从队列取任务并执行（支持本机多进程并发）。
- `collector`：收集结果到 CSV 并打印统计摘要（若业务层实现）。
- `submit-task`：提交单个任务，便于调试。
- `all`：在一台机器上一键启动 manager + workers + collector（可选 monitor）。

**示例**

```bash
ml-ds manager --tasks 1000
ml-ds manager --config path/to/conf.json

ml-ds worker -c 4
ml-ds worker --redis-url redis://host:6379/0 -c 8

ml-ds collector --max-results 1000
ml-ds collector --output-file results_custom.csv

ml-ds submit-task --model-type tree --hyperparameters '{"max_depth": 5}'
ml-ds submit-task --task-json '{"model_type":"forest","hyperparameters":{"n_estimators":200}}'

ml-ds all --tasks 200 -w 4 --monitor
```

**常用参数说明（节选）**

- `-w` / `--workers`：`ml-ds all` 模式下本机启动的 worker 进程数。
- `-c` / `--concurrency`：`ml-ds worker` 的本机并发进程数；`0` 表示按 CPU 核心数自动设置。
- `-t` / `--tasks`：下发的任务数量；用于 `ml-ds manager` 与 `ml-ds all`。
- `-i` / `--interval`：监控刷新间隔（秒）；用于 `ml-ds monitor`。
- `--monitor`：在 `ml-ds all` 中同时启用监控进程。
- `--max-results`：`ml-ds collector` 最多收集的结果条数；为空时持续收集到队列长期为空。
- `--output-file`：`ml-ds collector` 自定义 CSV 文件名（默认带时间戳）。
- `--redis-url`：统一指定 Redis 连接（例如 `redis://host:6379/0`、`redis://:password@host:6379/0`）。
- `--redis-host` / `--redis-port` / `--redis-db` / `--redis-password`：分别覆盖 `--redis-url` 的对应部分。

**`redis-url` 原理与限制**

- 解析位置：CLI 入口会解析 `--redis-url` 并注入环境变量（`REDIS_HOST/REDIS_PORT/REDIS_DB/REDIS_PASSWORD`）。
- 生效机制：运行时通过配置函数把环境变量转换为连接池（参考 `config/settings.py` 的 `build_redis_config()`）。
- 队列读写：任务以二进制（`pickle + zlib` 压缩）写入/读取；结果以 JSON 字符串写入/读取；`decode_responses=False` 防止 UTF-8 解码错误。
- 当前限制：
	- 支持 `redis://`；接受 `rediss://` 但暂未启用 TLS 参数（若需 TLS 可扩展 `ssl=True` 等配置）。
	- 不处理 URL 查询参数（如 `?timeout=...`）；如需自定义超时需在代码层配置。
	- 不支持 Sentinel/Cluster；目标是直连单实例。
	- URL 中用户名忽略（仅读取密码）；如需 ACL 用户名，需要扩展解析与连接配置。

**常见组合与模板**

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

### 6. 故障排查（CLI）

- 无法连接 Redis：确认已启动 `redis-server`；远程用 `--redis-url redis://host:6379/0` 或加 `--redis-host/--redis-port`。
- 端口占用：6379 冲突时，使用 `redis-server --port 6380` 并在 CLI 中同步指定 `--redis-port 6380`。
- 没有结果输出：确认至少一个 `ml-ds worker` 正在运行，且先运行了 `ml-ds manager`。
- 依赖问题：先 `python -m pip install -U pip`；必要时使用国内源 `-i https://pypi.tuna.tsinghua.edu.cn/simple`。

### 7. 开发者入口与帮助

- 开发调试可直接使用 Python 入口（与 CLI 等价）：
	- 入口脚本：run.py（子命令 all/manager/worker/collector/monitor）。
	- 配置：config/settings.py（Redis、队列名等）。
	- 组件：manager/、workers/、monitor/、utils/。

示例：

```bash
python run.py all --tasks 100 --workers 2 --monitor
```

- 快速查询 CLI 帮助：

```bash
ml-ds --help
ml-ds worker --help
ml-ds manager --help
ml-ds collector --help
ml-ds all --help

# 开发者入口帮助（与 CLI 等价）
python run.py -h
```


