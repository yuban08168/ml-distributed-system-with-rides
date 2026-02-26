"""基于 sklearn 的内置机器学习执行器示例。

目标：让最终使用者无需写任何 Python 代码，只需：
- 准备好数据集 data/boston_housing.csv；
- 用自带的 manager 生成任务（包含 model_type / hyperparameters）；
- 通过环境变量 MLDS_EXECUTOR 指向本模块的 execute_task；
即可完成一轮分布式超参搜索。
"""
from typing import Dict, Any
import os
import time

import numpy as np
import pandas as pd
from sklearn.model_selection import cross_validate, train_test_split
from sklearn.linear_model import LinearRegression, Ridge
from sklearn.tree import DecisionTreeRegressor
from sklearn.ensemble import RandomForestRegressor, GradientBoostingRegressor

from config.settings import DATA_DIR, MODEL_CONFIG


_DATA_CACHE = None  # 简单缓存，避免每个任务都重新读数据


def _load_boston() -> Dict[str, Any]:
    """加载数据集。

    优先读取环境变量指定的路径和目标列：
    - MLDS_DATA_CSV: 数据 CSV 路径（默认 data/boston_housing.csv）；
    - MLDS_TARGET_COL: 目标列名（默认 MEDV）。

    这样你可以在不改代码的情况下，用任意新数据集验证：

    export MLDS_DATA_CSV="/path/to/your.csv"
    export MLDS_TARGET_COL="target_column_name"
    """

    global _DATA_CACHE
    if _DATA_CACHE is not None:
        return _DATA_CACHE

    default_path = DATA_DIR / "boston_housing.csv"
    csv_path_str = os.getenv("MLDS_DATA_CSV", str(default_path))
    target_col = os.getenv("MLDS_TARGET_COL", "MEDV")

    csv_path = os.path.expanduser(csv_path_str)
    if not os.path.exists(csv_path):
        raise RuntimeError(f"找不到数据文件: {csv_path}")

    # 尝试按常规 CSV 读取
    df = pd.read_csv(csv_path)
    if target_col not in df.columns:
        # 兼容当前仓库自带的 boston_housing 数据：原始文件是空格分隔且无表头
        # 当使用默认路径且未找到目标列时，尝试按空格分隔重新读取并补上标准列名
        default_boston_path = str(default_path)
        if os.path.abspath(csv_path) == os.path.abspath(default_boston_path):
            df_raw = pd.read_csv(csv_path, delim_whitespace=True, header=None)
            # 波士顿房价数据应有 14 列
            if df_raw.shape[1] == 14:
                df_raw.columns = [
                    "CRIM",
                    "ZN",
                    "INDUS",
                    "CHAS",
                    "NOX",
                    "RM",
                    "AGE",
                    "DIS",
                    "RAD",
                    "TAX",
                    "PTRATIO",
                    "B",
                    "LSTAT",
                    "MEDV",
                ]
                df = df_raw
            else:
                raise RuntimeError(
                    f"{csv_path} 格式异常：期望 14 列（含 MEDV），实际为 {df_raw.shape[1]} 列；"
                    "请检查文件或使用 MLDS_DATA_CSV 指定自定义数据集。"
                )
        else:
            raise RuntimeError(f"{csv_path} 中必须包含目标列 {target_col}")

    # 目标列
    y = df[target_col]
    # 若目标列为非数值类型（例如字符串标签），自动做简单编码
    if not np.issubdtype(y.dtype, np.number):
        y = pd.Series(pd.factorize(y)[0], name=target_col)

    # 特征列：仅保留数值型特征，自动丢弃字符串/类别型列
    X_raw = df.drop(columns=[target_col])
    X = X_raw.select_dtypes(include=["number"]).copy()
    if X.empty:
        raise RuntimeError(
            "数据集中除目标列外没有可用的数值特征列；"
            "请在外部先做特征工程或手工构造数值特征。"
        )

    _DATA_CACHE = {"X": X, "y": y}
    return _DATA_CACHE


def _create_model(model_type: str, hyperparameters: Dict[str, Any]):
    """根据任务中的 model_type / hyperparameters 创建 sklearn 模型实例。"""

    if model_type == "linear":
        return LinearRegression(
            fit_intercept=hyperparameters.get("fit_intercept", True)
        )
    if model_type == "ridge":
        return Ridge(alpha=hyperparameters.get("alpha", 1.0))
    if model_type == "tree":
        return DecisionTreeRegressor(
            max_depth=hyperparameters.get("max_depth"),
            min_samples_split=hyperparameters.get("min_samples_split", 2),
            min_samples_leaf=hyperparameters.get("min_samples_leaf", 1),
            random_state=MODEL_CONFIG["random_state"],
        )
    if model_type == "forest":
        return RandomForestRegressor(
            n_estimators=hyperparameters.get("n_estimators", 100),
            max_depth=hyperparameters.get("max_depth"),
            min_samples_split=hyperparameters.get("min_samples_split", 2),
            min_samples_leaf=hyperparameters.get("min_samples_leaf", 1),
            random_state=MODEL_CONFIG["random_state"],
            n_jobs=1,
        )
    if model_type == "gradient":
        return GradientBoostingRegressor(
            n_estimators=hyperparameters.get("n_estimators", 100),
            learning_rate=hyperparameters.get("learning_rate", 0.1),
            max_depth=hyperparameters.get("max_depth", 3),
            random_state=MODEL_CONFIG["random_state"],
        )

    raise ValueError(f"未知模型类型: {model_type}")


def execute_task(task: Dict[str, Any]) -> Dict[str, Any]:
    """一个开箱即用的 "ML 训练 + 评估" 执行器实现。

    期望任务结构：
    - task["model_type"]: "linear" / "tree" / "forest" / "gradient" 等；
    - task["hyperparameters"]: dict, 对应各模型的超参数；

    返回结果结构（会被调度层再包一层元数据）：
    - model_type / hyperparameters 原样回传；
    - metrics: 各种评估指标（RMSE/MSE/MAE/R2 + 训练耗时）。
    """

    data = _load_boston()
    X, y = data["X"], data["y"]

    model_type = task.get("model_type", "tree")
    hyperparameters = task.get("hyperparameters", {})

    model = _create_model(model_type, hyperparameters)

    start = time.time()

    # 使用部分数据子集做评估，兼顾速度与效果
    if len(X) > 1000:
        X_sample, _, y_sample, _ = train_test_split(
            X,
            y,
            test_size=0.7,
            random_state=MODEL_CONFIG["random_state"],
        )
    else:
        X_sample, y_sample = X, y

    cv_results = cross_validate(
        model,
        X_sample,
        y_sample,
        cv=min(MODEL_CONFIG["cv_folds"], len(X_sample)),
        scoring={
            "neg_mse": "neg_mean_squared_error",
            "neg_mae": "neg_mean_absolute_error",
            "r2": "r2",
        },
        n_jobs=1,
        return_train_score=False,
    )

    mse_scores = -cv_results["test_neg_mse"]
    mae_scores = -cv_results["test_neg_mae"]
    r2_scores = cv_results["test_r2"]
    rmse_scores = np.sqrt(mse_scores)

    elapsed = time.time() - start

    return {
        "model_type": model_type,
        "hyperparameters": hyperparameters,
        "metrics": {
            "mean_rmse": float(np.mean(rmse_scores)),
            "std_rmse": float(np.std(rmse_scores)),
            "mean_mse": float(np.mean(mse_scores)),
            "std_mse": float(np.std(mse_scores)),
            "mean_mae": float(np.mean(mae_scores)),
            "std_mae": float(np.std(mae_scores)),
            "mean_r2": float(np.mean(r2_scores)),
            "std_r2": float(np.std(r2_scores)),
            "training_time": elapsed,
        },
    }
