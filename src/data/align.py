"""
多源对齐模块。

输入：
    logs_window.parquet     : window_5min, service, log_cnt, error_cnt, warn_cnt
    traces_window.parquet   : window_5min, service, span_cnt, error_cnt, avg_duration_ms, max_duration_ms
    metrics_apm_service     : window_5min, entity, metric, mean, max, min, std, cnt
    metrics_infra_*         : window_5min, entity, metric, mean, max, min, std, cnt
    metrics_gaia_full       : timestamp, entity, metric, value, window_5min（流式读）

输出：
    aligned_samples.parquet  : window_5min, service, log_*, trace_*, metric_*
"""
from __future__ import annotations

import os
import re
import argparse
import pandas as pd
import pyarrow.parquet as pq


# ==========================================================
# metric 筛选清单（按 field_mapping.md 的保留策略）
# ==========================================================
GAIA_METRIC_KEEP_NAMES = {
    # docker CPU
    "docker_cpu_usage", "docker_cpu_ticks_total",
    # docker 内存
    "docker_memory_usage_pct", "docker_memory_usage_total", "docker_memory_fail_count",
    # docker 磁盘
    "docker_diskio_read_bytes", "docker_diskio_write_bytes",
    "docker_diskio_write_rate", "docker_diskio_write_queued",
    # docker 网络
    "docker_network_in_bytes", "docker_network_out_bytes",
    "docker_network_in_errors", "docker_network_out_errors",
    "docker_network_in_dropped", "docker_network_out_dropped",
    # system CPU
    "system_cpu_user_pct", "system_cpu_system_pct",
    "system_cpu_iowait_pct", "system_cpu_idle_pct",
    # system 负载
    "system_load_norm_1", "system_load_norm_5", "system_load_norm_15",
    # system 磁盘
    "system_diskio_read_bytes", "system_diskio_write_bytes",
    # system 文件系统
    "system_fsstat_total_size_used", "system_fsstat_total_size_free",
    # system 内存
    "system_memory_used_bytes", "system_memory_used_pct",
    # system 网络
    "system_network_summary_tcp_CurrEstab",
    "system_network_summary_tcp_EstabResets",
    "system_network_summary_tcp_ListenDrops",
    # redis
    "redis_info_replication_master_offset",
    "redis_info_stats_expired_keys",
    "redis_info_stats_evicted_keys",
    "redis_info_stats_keyspace_hits",
    "redis_info_stats_keyspace_misses",
    "redis_info_clients_connected_clients",
    "redis_info_memory_used_memory",
    "redis_info_memory_used_memory_peak",
    # zookeeper
    "zookeeper_avg_latency",
    "zookeeper_max_latency",
    "zookeeper_min_latency",
    "zookeeper_outstanding_requests",
    "zookeeper_packets_received",
    "zookeeper_packets_sent",
    "zookeeper_num_alive_connections",
}


def is_gaia_metric_kept(metric: str) -> bool:
    return metric in GAIA_METRIC_KEEP_NAMES

# ==========================================================
# entity 归一化
# ==========================================================
def normalize_entity(entity: str) -> str:
    """
    AIOps：不变（service/pod/node 已经是规范名）
    GAIA：
      dbservice1_0.0.0.4        → dbservice1
      system_0.0.0.1_0.0.0.1    → system_0.0.0.1
      system_0.0.0.1            → system_0.0.0.1
      redis_0.0.0.3             → redis
    """
    if pd.isna(entity):
        return entity
    e = str(entity)

    # GAIA 双 IP：system_<IP>_<IP> → system_<IP>
    m = re.match(r"^(system_\d+\.\d+\.\d+\.\d+)_\d+\.\d+\.\d+\.\d+$", e)
    if m:
        return m.group(1)

    # 业务服务：<service>_<IP> → <service>
    m = re.match(r"^([a-zA-Z][a-zA-Z0-9\-]*)_\d+\.\d+\.\d+\.\d+$", e)
    if m:
        return m.group(1)

    return e


# ==========================================================
# 读 window 聚合（logs / traces）
# ==========================================================
def load_window(path: str) -> pd.DataFrame:
    if not os.path.exists(path):
        print(f"[skip] {path} 不存在")
        return pd.DataFrame()
    df = pd.read_parquet(path)
    print(f"[ok] {path}: {len(df)} rows")
    return df


# ==========================================================
# 读 AIOps metrics（小文件，一次读）
# ==========================================================
AIOPS_METRIC_FILES = [
    "outputs/demo/metrics_apm_service_full.parquet",
    "outputs/demo/metrics_apm_pod_full.parquet",
    "outputs/demo/metrics_infra_pod_full.parquet",
    "outputs/demo/metrics_infra_node_full.parquet",
    "outputs/demo/metrics_infra_tidb_full.parquet",
    "outputs/demo/metrics_other_full.parquet",
]


def load_aiops_metrics() -> pd.DataFrame:
    dfs = []
    for f in AIOPS_METRIC_FILES:
        if not os.path.exists(f):
            print(f"[skip] {f}")
            continue
        df = pd.read_parquet(f)
        print(f"[ok] {f}: {len(df)} rows")
        dfs.append(df)
    if not dfs:
        return pd.DataFrame()
    m = pd.concat(dfs, ignore_index=True)

    # 宽表指标展开成 5min 窗口的均值
    agg = (
        m.groupby(["window_5min", "entity", "metric"], as_index=False)
        .agg(value=("value", "mean"))
    )
    # 长表 → 宽表：每个 metric 一列
    wide = agg.pivot_table(
        index=["window_5min", "entity"],
        columns="metric",
        values="value",
        aggfunc="mean",
    ).reset_index()
    wide.columns = [f"metric_{c}" if c not in ("window_5min", "entity") else c
                    for c in wide.columns]
    return wide


# ==========================================================
# 读 GAIA metrics（5.3G，流式读 + 筛选 + 归一化）
# ==========================================================
def load_gaia_metrics(metric_filter: bool = True) -> pd.DataFrame:
    path = "outputs/demo/metrics_gaia_full.parquet"
    if not os.path.exists(path):
        print(f"[skip] {path}")
        return pd.DataFrame()

    pf = pq.ParquetFile(path)
    batches = []
    n_read = 0
    n_keep = 0

    for batch in pf.iter_batches(batch_size=500_000):
        df = batch.to_pandas()
        n_read += len(df)

        # 筛选 metric
        if metric_filter:
            df = df[df["metric"].apply(is_gaia_metric_kept)]
        if df.empty:
            continue

        # 归一化 entity
        df["entity"] = df["entity"].apply(normalize_entity)

        # 按 window_5min + entity + metric 聚合
        agg = (
            df.groupby(["window_5min", "entity", "metric"], as_index=False)
            .agg(value=("value", "mean"))
        )
        batches.append(agg)
        n_keep += len(agg)

        if n_read % 50_000_000 == 0:
            print(f"  已读 {n_read:,} 行，保留 {n_keep:,} 行")

    if not batches:
        return pd.DataFrame()

    merged = pd.concat(batches, ignore_index=True)
    merged = (
        merged.groupby(["window_5min", "entity", "metric"], as_index=False)
        .agg(value=("value", "mean"))
    )
    print(f"[ok] GAIA metric 全量读 {n_read:,} 行，筛选后 {len(merged):,} 行")

    wide = merged.pivot_table(
        index=["window_5min", "entity"],
        columns="metric",
        values="value",
        aggfunc="mean",
    ).reset_index()
    wide.columns = [f"metric_{c}" if c not in ("window_5min", "entity") else c
                    for c in wide.columns]
    return wide


# ==========================================================
# 对齐主逻辑
# ==========================================================
def align(
    logs_path: str,
    traces_path: str,
    metrics_df: pd.DataFrame,
    entity_col: str = "entity",
) -> pd.DataFrame:
    """
    traces 作 base，left join logs 和 metrics。
    - traces 是样本骨架（覆盖较全）
    - logs 缺失表示无异常日志，填 0
    - metrics 缺失保留 NaN
    """
    logs = load_window(logs_path)
    traces = load_window(traces_path)

    if traces.empty and logs.empty:
        return pd.DataFrame()

    # 重命名，避免列冲突
    if not logs.empty:
        logs = logs.rename(columns={
            "log_cnt": "log_log_cnt",
            "error_cnt": "log_error_cnt",
            "warn_cnt": "log_warn_cnt",
        })
    if not traces.empty:
        traces = traces.rename(columns={
            "span_cnt": "trace_span_cnt",
            "error_cnt": "trace_error_cnt",
            "avg_duration_ms": "trace_avg_duration_ms",
            "max_duration_ms": "trace_max_duration_ms",
        })

    key = ["window_5min", "service"]

    # 优先 traces 作 base，否则 logs 作 base
    if not traces.empty:
        base = traces
        if not logs.empty:
            base = base.merge(logs, on=key, how="left")
    else:
        base = logs

    # logs 缺失填 0（表示该窗口无异常日志）
    for c in ["log_log_cnt", "log_error_cnt", "log_warn_cnt"]:
        if c in base.columns:
            base[c] = base[c].fillna(0).astype(int)

    base["service"] = base["service"].fillna("unknown")

    # metrics left join
    if not metrics_df.empty:
        m = metrics_df.rename(columns={entity_col: "service"})
        base = base.merge(m, on=["window_5min", "service"], how="left")

    # 统计来源数：trace 恒有，logs 看是否 >0，metrics 看是否非全 NaN
    base["source_count"] = 1  # trace 默认 1
    if "log_log_cnt" in base.columns:
        base["source_count"] += (base["log_log_cnt"] > 0).astype(int)
    metric_cols = [c for c in base.columns if c.startswith("metric_")]
    if metric_cols:
        base["source_count"] += base[metric_cols].notna().any(axis=1).astype(int)

    return base.sort_values(["window_5min", "service"]).reset_index(drop=True)

# ==========================================================
# CLI
# ==========================================================
def main():
    p = argparse.ArgumentParser(description="Multi-source alignment.")
    p.add_argument("--dataset", required=True, choices=["aiops", "gaia"])
    p.add_argument("--logs", required=True)
    p.add_argument("--traces", required=True)
    p.add_argument("--output", required=True)
    p.add_argument("--no-metric-filter", action="store_true",
                   help="GAIA 不筛选 metric（调试用）")
    args = p.parse_args()

    print(f"\n=== 加载 {args.dataset} window 聚合 ===")
    if args.dataset == "aiops":
        metrics_df = load_aiops_metrics()
    else:
        metrics_df = load_gaia_metrics(metric_filter=not args.no_metric_filter)

    print(f"\n=== 对齐 ===")
    aligned = align(
        logs_path=args.logs,
        traces_path=args.traces,
        metrics_df=metrics_df,
    )

    if aligned.empty:
        print("[warn] 对齐结果为空")
        return

    aligned.to_parquet(args.output, index=False)

    print(f"\n=== 对齐结果 ===")
    print(f"rows:         {len(aligned):,}")
    print(f"windows:      {aligned['window_5min'].nunique()}")
    print(f"services:     {aligned['service'].nunique()}")
    print(f"metric cols:  {len([c for c in aligned.columns if c.startswith('metric_')])}")
    print(f"source_count 分布:")
    print(aligned["source_count"].value_counts().to_string())
    print(f"output:       {args.output}")


if __name__ == "__main__":
    main()