## 分布式机器学习任务调度系统（Redis + 微型机集群）

本项目实现了一个基于 Redis 队列的分布式机器学习任务调度框架，用于在多台机器/进程上同时对同一数据集（本实验为波士顿房价数据集）进行大规模超参数搜索和模型评估。

核心思想：

- **任务管理器（manager）**：生成不同模型及其超参数组合，将训练任务压缩后推入 Redis 任务队列。
- **工作节点（worker）**：从任务队列中取出任务，加载波士顿房价数据集，在本地训练并评估模型，将结果写入 Redis 结果队列。
- **结果收集器（collector）**：从结果队列拉取结果，增量写入 CSV 文件，并生成简单的统计报告（如最佳 RMSE、各模型表现等）。
- **监控（monitor）**：定期统计 Redis 队列长度、活跃 worker 数量以及系统 CPU/内存使用情况。

适用场景：在多台配置较低的“微型机”（树莓派、小型服务器、实验室多台 PC）上协同完成几万到几十万级别的机器学习训练任务。

---

## 项目结构概览

主要目录与模块说明：

- `run.py`：项目入口脚本，提供以下子命令：
  - `all`：在一个进程中启动 manager + 多个 worker + collector（开发测试方便）。
  - `manager`：只启动任务管理器，下发任务到 Redis 队列。
  - `worker`：启动单个工作节点，从队列中取任务并训练模型。
  - `collector`：启动结果收集器，从结果队列拉取结果并写入CSV。
  - `monitor`：系统与队列监控。
- `config/settings.py`：Redis 配置、队列名称、路径、模型相关配置等。
- `utils/redis_client.py`：Redis 客户端封装，负责任务/结果的压缩存储、worker 注册与队列统计等。
- `manager/task_manager.py`：任务管理器，根据给定任务数随机采样模型超参数，并批量推入 Redis。
- `workers/worker.py`：worker 实现，负责加载数据集、创建模型、交叉验证评估并推送结果。
- `manager/result_collector.py`：结果收集器，实现增量写入 CSV 以及收尾统计报告。
- `monitor/monitor.py`：监控模块，周期性打印系统与 Redis 队列状态，并写入 Redis 监控键。
- `data/`：数据目录，本实验使用 `boston_housing.csv`。
- `results/`：结果输出目录，collector 会在此生成 `results_*.csv` 以及统计报告。

---

## 环境准备

### 1. Python 虚拟环境

建议在项目目录下使用已提供的虚拟环境目录 `distributed/`，或自建一个 venv：

```bash
cd /home/stu/code/ml-distributed-system

# 如已有自带 venv，可直接激活
source distributed/bin/activate

# 或者自行创建新的虚拟环境
python -m venv venv
source venv/bin/activate
```

### 2. 安装依赖

```bash
cd /home/stu/code/ml-distributed-system
pip install -r requirements.txt
```

> 说明：`requirements.txt` 中使用 `scikit-learn>=1.2.0`，因此不再自带 `load_boston`，本项目已在 worker 端实现了兼容逻辑（详见下文“数据集准备”）。

### 3. 启动 Redis 服务

在本机启动 Redis（本项目默认连接 `localhost:6379`，可通过环境变量覆盖）：

```bash
redis-server
```

如需自定义地址/端口，可在运行 python 程序前设置：

```bash
export REDIS_HOST=localhost
export REDIS_PORT=6379
```

---

## 数据集准备（波士顿房价）

当前 worker 支持两种 Boston 房价数据格式，文件路径统一为：

```text
data/boston_housing.csv
```

### 1. 带表头的标准 CSV（推荐）

- 第一行是列名，必须包含一列名为 `MEDV` 的目标列。
- 其它列为特征，例如：`CRIM, ZN, INDUS, CHAS, NOX, RM, AGE, DIS, RAD, TAX, PTRATIO, B, LSTAT, MEDV`。

示例（前几列）：

```csv
CRIM,ZN,INDUS,CHAS,NOX,RM,AGE,DIS,RAD,TAX,PTRATIO,B,LSTAT,MEDV
0.00632,18.00,2.31,0,0.538,6.575,65.2,4.09,1,296,15.3,396.9,4.98,24.0
...
```

### 2. 无表头、空白分隔的原始 Boston 数据

你当前使用的数据即为这种格式：

- 没有表头；
- 每行 14 个数值；
- 使用空格或制表符分隔；
- 最后一列为房价（目标值）。

`workers/worker.py` 中的 `load_data()` 已做了兼容：

- 若检测到 CSV 无 `MEDV` 列，则按无表头 + 14 列进行解析；
- 自动为 14 列赋予列名：
  `['CRIM','ZN','INDUS','CHAS','NOX','RM','AGE','DIS','RAD','TAX','PTRATIO','B','LSTAT','MEDV']`；
- 使用前 13 列作为特征 `X`，最后一列 `MEDV` 作为标签 `y`。

---

## 快速上手机器训练一次

下面是最常用的“三步走”方式：manager 下发任务 → 多个 worker 训练 → collector 收集结果。

### 步骤 1：下发一批任务（manager）

在终端 1 中，确保虚拟环境已激活：

```bash
cd /home/stu/code/ml-distributed-system
python run.py manager --tasks 100
```

看到输出类似：

```text
✅ Redis连接成功: localhost:6379
🚀 启动任务管理器，生成 100 个任务...
任务提交完成！成功: 100, 失败: 0
```

说明 100 个训练任务已经写入 Redis 任务队列。

### 步骤 2：启动一个或多个 worker

在终端 2、终端 3 等分别启动 worker（可以在同一台机器，也可以在多台机器，只要这些机器都能访问同一个 Redis）：

```bash
cd /home/stu/code/ml-distributed-system
python run.py worker --worker-id worker-1

# 可在另一个终端再起一个
python run.py worker --worker-id worker-2
```

正常情况下，worker 日志中会看到：

```text
加载波士顿房价数据集...
数据集加载完成: 506 样本, 13 特征
工作节点 worker-1 处理任务: task_0
工作节点 worker-1 完成任务: task_0, RMSE: ...
...
```

### 步骤 3：启动结果收集器（collector）

在终端 4 中：

```bash
cd /home/stu/code/ml-distributed-system
python run.py collector --max-results 100
```

collector 会从 Redis 结果队列中不断拉取训练结果，按批写入 `results/` 下的 CSV 文件，例如：

```text
results/results_1733900000.csv
```

收集结束后，会自动打印统计报告，包括：

- 总任务数
- 平均/最佳/最差 RMSE
- 各模型类型的统计（均值 RMSE、样本数等）
- 最佳模型的任务 ID、模型类型和超参数

---

## 一键启动所有组件（开发模式）

在单机上进行开发调试时，可以用 `all` 子命令一键启动 manager + 多 worker + collector（以及可选的监控）：

```bash
cd /home/stu/code/ml-distributed-system
python run.py all --tasks 100 --workers 2 --monitor
```

该命令会：

- 启动一个子进程作为任务管理器，提交 `--tasks` 个训练任务；
- 启动 `--workers` 个 worker 进程并发训练；
- 启动一个结果收集器进程，将所有结果写入 CSV；
- 如指定 `--monitor`，还会额外启动监控进程定期输出系统和队列状态。

按 `Ctrl+C` 可以一键停止所有子进程。

---

## 分布式/多机部署思路

由于任务和结果都通过 Redis 队列传递，且数据集在每台 worker 本地加载，因此很容易扩展到多机环境：

1. 在一台服务器上部署 Redis（暴露给局域网）。
2. 将本项目代码和 `data/boston_housing.csv` 拷贝到多台“微型机”上，并在每台机器上：
	- 配置 `REDIS_HOST` 指向 Redis 所在服务器；
	- 启动若干 worker：`python run.py worker --worker-id <unique-id>`。
3. 在其中任意一台机器上启动 manager 和 collector 即可共享同一任务/结果队列，实现真正的多机分布式训练。

---

## 常见问题排查

1. **collector 一直提示“队列为空，等待新结果”**
	- 确认至少有一个 worker 正常运行且没有报错退出；
	- 确认 manager 已经提交了任务（可以再次运行 `python run.py manager --tasks 100`）；
	- 确认 Redis 连接配置一致（`REDIS_HOST`、`REDIS_PORT`）；
	- 确认当前 Python 环境已安装 `redis`、`numpy`、`pandas`、`scikit-learn` 等依赖，并且已经激活对应虚拟环境。

2. **获取任务失败：`'utf-8' codec can't decode byte ...`**
	- 说明 Redis 客户端在尝试用 UTF-8 解码二进制任务数据。
	- 本项目已在 `config/settings.py` 中将 `REDIS_CONFIG['decode_responses']` 设为 `False`，确保任务数据以 bytes 形式读写，并在内部使用 `pickle + zlib` 进行序列化与压缩。
	- 如有自行修改 Redis 配置，请确保保持这一设置。

3. **无法加载 Boston 数据集**
	- 检查 `data/boston_housing.csv` 是否存在；
	- 若为带表头 CSV，确保存在 `MEDV` 列；
	- 若为无表头数据，确认每行有 14 列数值，并使用空白分隔。

---

## 后续扩展方向

- 替换或扩展数据集：不仅限于 Boston 房价，可将数据加载逻辑参数化。
- 增加更多模型：如 XGBoost、LightGBM 等（注意内存与依赖体积）。
- 完善任务重试与超时机制：根据 `TASK_CONFIG['max_retries']` 做任务失败自动重试。
- 引入可视化监控：基于 Prometheus / Grafana 对 Redis 队列与 worker 状态进行图形化监控。

欢迎在此基础上继续扩展实验，构建自己的分布式机器学习调度平台。

## 12.25 更新

- 注释掉了无用的gc，time.sleep(0.1)以提高效率
- 优化了退出机制，现在可以自动退出，并且每次运行前清空队列，避免读到就结果
- 现在连续多次不能从队列中拿到队伍时，会自动退出
- 删除了之前失败的docker尝试
