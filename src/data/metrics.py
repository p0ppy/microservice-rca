"""
指标加载与窗口聚合模块。

支持三种指标源：
1. AIOps 2025 apm/service、apm/pod（宽表，一分钟一行）
2. AIOps 2025 infra/*（窄表，同一时刻多行，需按实体聚合）
3. GAIA MicroSS/metric（一指标一文件，timestamp,value）

统一输出 schema：
    timestamp    : datetime64[ns, UTC]
    entity       : str        (服务名 / Pod 名 / 节点名)
    entity_type  : str        (service / pod / node / tidb)
    metric       : str        (英文指标名)
    value        : float
    window_5min  : datetime64[ns, UTC]
"""
from __future__ import annotations

import os
import glob
import argparse
import pandas as pd


INFRA_META_COLS = {
    "time", "cf", "device", "instance", "kpi_key", "kpi_name",
    "kubernetes_node", "mountpoint", "namespace", "object_type",
    "pod", "sql_type", "type",
}


def _glob_files(pattern: str) -> list[str]:
    files = glob.glob(pattern, recursive=True)
    return [f for f in files if not os.path.basename(f).startswith("._")]


# ==========================================================
# AIOps 2025 apm（宽表）
# ==========================================================
APM_KPI_COLS = [
    "request", "response", "rrt", "rrt_max",
    "error", "error_ratio",
    "client_error", "client_error_ratio",
    "server_error", "server_error_ratio",
    "timeout",
]


def load_aiops_apm(path: str) -> pd.DataFrame:
    """
    读单个 apm/service 或 apm/pod 文件。
    宽表 → 长表：每个 KPI 一行。
    """
    df = pd.read_parquet(path)

    df = df.rename(columns={"time": "timestamp", "object_id": "entity"})
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True, errors="coerce")
    df["entity_type"] = df["object_type"].astype(str).str.lower()

    kpi_cols = [c for c in APM_KPI_COLS if c in df.columns]
    if not kpi_cols:
        return pd.DataFrame()

    melted = df.melt(
        id_vars=["timestamp", "entity", "entity_type"],
        value_vars=kpi_cols,
        var_name="metric",
        value_name="value",
    )
    melted = melted.dropna(subset=["timestamp", "entity", "value"])
    melted["window_5min"] = melted["timestamp"].dt.floor("5min")
    return melted[[
        "timestamp", "entity", "entity_type", "metric", "value", "window_5min"
    ]]


# ==========================================================
# AIOps 2025 infra（窄表）
# ==========================================================
def _find_infra_value_col(df: pd.DataFrame) -> str | None:
    """排除元数据列后，剩下的第一列就是值列。"""
    candidates = [c for c in df.columns if c not in INFRA_META_COLS]
    return candidates[0] if candidates else None


def _clean_null(series: pd.Series) -> pd.Series:
    """把字符串 'null' / 'None' / 'nan' 统一成 pd.NA。"""
    return series.astype(str).replace({"null": pd.NA, "None": pd.NA, "nan": pd.NA})


def _pick_infra_entity(df: pd.DataFrame) -> pd.Series:
    """infra_pod 用 pod；infra_node 用 kubernetes_node；兜底 instance。"""
    if "pod" in df.columns:
        pod_series = _clean_null(df["pod"])
        if pod_series.notna().any():
            return pod_series

    if "kubernetes_node" in df.columns:
        node_series = _clean_null(df["kubernetes_node"])
        if node_series.notna().any():
            return node_series

    if "instance" in df.columns:
        return df["instance"].astype(str)

    return pd.Series([None] * len(df), index=df.index)


def load_aiops_infra(path: str) -> pd.DataFrame:
    """
    读单个 infra/* parquet。
    窄表：同一 timestamp 多行（device/mountpoint/type 维度不同），按实体+时间聚合。
    """
    df = pd.read_parquet(path)

    value_col = _find_infra_value_col(df)
    if value_col is None:
        return pd.DataFrame()

    # metric 优先用 kpi_key
    if "kpi_key" in df.columns and df["kpi_key"].notna().any():
        metric = str(df["kpi_key"].dropna().iloc[0])
    else:
        metric = value_col

    out = pd.DataFrame({
        "timestamp": pd.to_datetime(df["time"], utc=True, errors="coerce"),
        "entity": _pick_infra_entity(df),
        "entity_type": df.get("object_type", pd.Series(["unknown"] * len(df))).astype(str).str.lower(),
        "metric": metric,
        "value": pd.to_numeric(df[value_col], errors="coerce"),
    })
    out = out.dropna(subset=["timestamp", "entity", "value"])

    # 同一实体同一时间多行，按 mean 聚合（累加型指标后续可改为 sum）
    out = (
        out.groupby(["timestamp", "entity", "entity_type", "metric"], as_index=False)
        .agg(value=("value", "mean"))
    )
    out["window_5min"] = out["timestamp"].dt.floor("5min")
    return out


# ==========================================================
# GAIA metric（占位，周四补）
# ==========================================================
def load_gaia_metric(path: str) -> pd.DataFrame:
    raise NotImplementedError("GAIA metric 周四实现")


# ==========================================================
# 统一目录加载
# ==========================================================
def _load_dir(pattern: str, loader_fn) -> pd.DataFrame:
    files = _glob_files(pattern)
    if not files:
        return pd.DataFrame()

    dfs = []
    for f in files:
        try:
            d = loader_fn(f)
            if not d.empty:
                dfs.append(d)
                print(f"[ok] {os.path.basename(f)}: {len(d)} rows")
        except Exception as e:
            print(f"[skip] {f}: {e}")

    if not dfs:
        return pd.DataFrame()
    return pd.concat(dfs, ignore_index=True)


# ==========================================================
# 窗口聚合
# ==========================================================
def window_features(df: pd.DataFrame) -> pd.DataFrame:
    """按 (window_5min, entity, metric) 聚合统计量。"""
    cols = ["window_5min", "entity", "metric", "mean", "max", "min", "std", "cnt"]
    if df.empty:
        return pd.DataFrame(columns=cols)

    out = (
        df.groupby(["window_5min", "entity", "metric"], as_index=False)
        .agg(
            mean=("value", "mean"),
            max=("value", "max"),
            min=("value", "min"),
            std=("value", "std"),
            cnt=("value", "size"),
        )
    )
    return out


# ==========================================================
# CLI
# ==========================================================
def main():
    p = argparse.ArgumentParser(description="Metric loader for AIOps 2025.")
    p.add_argument("--source", required=True, choices=["apm", "infra"])
    p.add_argument("--input", required=True, help="glob 模式")
    p.add_argument("--output", required=True)
    p.add_argument("--aggregate", action="store_true")
    p.add_argument("--max-files", type=int, default=None,
                   help="demo 时限制文件数")
    args = p.parse_args()

    if args.source == "apm":
        loader = load_aiops_apm
    else:
        loader = load_aiops_infra

    files = _glob_files(args.input)
    if args.max_files:
        files = files[:args.max_files]

    dfs = []
    for f in files:
        try:
            d = loader(f)
            if not d.empty:
                dfs.append(d)
                print(f"[ok] {os.path.basename(f)}: {len(d)} rows")
        except Exception as e:
            print(f"[skip] {f}: {e}")

    if not dfs:
        print("[warn] empty dataframe")
        return

    df = pd.concat(dfs, ignore_index=True)
    df.to_parquet(args.output, index=False)

    print(f"source      : {args.source}")
    print(f"rows        : {len(df)}")
    print(f"entities    : {df['entity'].nunique()}")
    print(f"metrics     : {df['metric'].nunique()}")
    print(f"time range  : {df['timestamp'].min()} -> {df['timestamp'].max()}")
    print(f"missing ts  : {df['timestamp'].isna().sum()}")
    print(f"output      : {args.output}")

    if args.aggregate:
        agg = window_features(df)
        agg_path = args.output.replace(".parquet", "_window.parquet")
        agg.to_parquet(agg_path, index=False)
        print(f"window rows : {len(agg)}")
        print(f"window out  : {agg_path}")


if __name__ == "__main__":
    main()