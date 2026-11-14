"""
调用链加载、派生与拓扑构建模块。

支持两种 trace 源：
1. AIOps 2025 Jaeger span（嵌套 references / tags / process）
2. GAIA MicroSS/trace（扁平表，parent_id 直接给出）

统一输出 schema：
    trace_id     : str
    span_id      : str
    parent_id    : str | None
    service      : str
    pod          : str | None
    node         : str | None
    operation    : str
    start_time   : datetime64[ns, UTC]
    duration_us  : float
    duration_ms  : float
    status       : str | None    (原始状态码)
    is_error     : bool
    span_kind    : str | None    (client / server)
    window_5min  : datetime64[ns, UTC]
    caller       : str           (派生：父 span 的 service)
    callee       : str           (派生：当前 span 的 service)
"""
from __future__ import annotations

import os
import glob
import json
import argparse
import pandas as pd
import networkx as nx


# ==========================================================
# 工具函数
# ==========================================================
def _glob_files(pattern: str) -> list[str]:
    files = glob.glob(pattern, recursive=True)
    return [f for f in files if not os.path.basename(f).startswith("._")]


def _to_iterable(v):
    """把 list / tuple / np.ndarray / None 统一成可迭代对象；否则返回 None。"""
    if v is None:
        return None
    try:
        iter(v)
        return v
    except TypeError:
        return None


def _extract_tag(tags, key):
    """从 AIOps tags 中取指定 key 的 value；兼容 list / ndarray / None。"""
    it = _to_iterable(tags)
    if it is None:
        return None
    for t in it:
        if isinstance(t, dict) and t.get("key") == key:
            return t.get("value")
    return None


def _extract_parent_span_id(references):
    """从 AIOps references 中取 CHILD_OF 父 span_id；兼容 list / ndarray / None。"""
    it = _to_iterable(references)
    if it is None:
        return None
    for r in it:
        if isinstance(r, dict) and r.get("refType") == "CHILD_OF":
            return r.get("spanID")
    return None


def _extract_process_field(process, field):
    """
    从 AIOps process 取字段。
    - field='service' → process.serviceName
    - field='pod'     → process.tags[key=name]
    - field='node'    → process.tags[key=node_name]
    """
    if process is None:
        return None
    if not isinstance(process, dict):
        # 兼容 ndarray 包一层 dict 的情况
        try:
            process = process.item() if hasattr(process, "item") else dict(process)
        except Exception:
            return None
    if field == "service":
        return process.get("serviceName")
    tag_key = {"pod": "name", "node": "node_name"}.get(field)
    if tag_key:
        return _extract_tag(process.get("tags"), tag_key)
    return None


# ==========================================================
# AIOps 2025 trace
# ==========================================================
def load_aiops_traces(path: str) -> pd.DataFrame:
    """读单个 AIOps 2025 trace-parquet。"""
    df = pd.read_parquet(path)

    df["trace_id"] = df["traceID"]
    df["span_id"] = df["spanID"]
    df["parent_id"] = df["references"].apply(_extract_parent_span_id)
    df["service"] = df["process"].apply(lambda p: _extract_process_field(p, "service"))
    df["pod"] = df["process"].apply(lambda p: _extract_process_field(p, "pod"))
    df["node"] = df["process"].apply(lambda p: _extract_process_field(p, "node"))
    df["operation"] = df["operationName"]
    df["span_kind"] = (
    df["tags"]
    .apply(lambda t: _extract_tag(t, "span.kind"))
    .apply(lambda x: x.lower() if isinstance(x, str) else x)
    )
    df["status"] = df["tags"].apply(lambda t: _extract_tag(t, "status.code"))

    # startTime 是微秒级 Unix 时间戳
    df["start_time"] = pd.to_datetime(df["startTime"], unit="us", utc=True, errors="coerce")
    df["duration_us"] = pd.to_numeric(df["duration"], errors="coerce")
    df["duration_ms"] = df["duration_us"] / 1000

    # status.code: 0=OK，非 0=ERROR
    df["is_error"] = pd.to_numeric(df["status"], errors="coerce").fillna(0).astype(int) != 0
    df["window_5min"] = df["start_time"].dt.floor("5min")

    keep = [
        "trace_id", "span_id", "parent_id", "service", "pod", "node",
        "operation", "start_time", "duration_us", "duration_ms",
        "status", "is_error", "span_kind", "window_5min",
    ]
    return df[keep]


# ==========================================================
# GAIA trace
# ==========================================================
def load_gaia_traces(path: str) -> pd.DataFrame:
    """读单个 GAIA trace_table_*.csv。"""
    df = pd.read_csv(path, dtype=str)

    df["trace_id"] = df["trace_id"]
    df["span_id"] = df["span_id"]
    # GAIA 的 root 用 "0"，统一成 None
    df["parent_id"] = df["parent_id"].replace({"0": None, 0: None})
    df["service"] = df["service_name"]
    df["pod"] = None
    df["node"] = df["host_ip"]
    df["operation"] = df["url"]
    df["span_kind"] = None
    df["status"] = df["status_code"]

    df["start_time"] = pd.to_datetime(df["start_time"], utc=True, errors="coerce")
    end_time = pd.to_datetime(df["end_time"], utc=True, errors="coerce")
    df["duration_us"] = (end_time - df["start_time"]).dt.total_seconds() * 1e6
    df["duration_ms"] = df["duration_us"] / 1000

    # status_code 是 HTTP 码，>=400 为异常
    df["is_error"] = pd.to_numeric(df["status"], errors="coerce").fillna(0).astype(int) >= 400
    df["window_5min"] = df["start_time"].dt.floor("5min")

    keep = [
        "trace_id", "span_id", "parent_id", "service", "pod", "node",
        "operation", "start_time", "duration_us", "duration_ms",
        "status", "is_error", "span_kind", "window_5min",
    ]
    return df[keep]


# ==========================================================
# 统一加载
# ==========================================================
def _load_dir(pattern: str, loader_fn, sample_per_window: int | None = None) -> pd.DataFrame:
    """
    逐文件加载，按 window_5min 分层抽样，保证每个窗口都有数据。
    - sample_per_window: 每个 5 分钟窗口最多保留多少条 span
    - 不设置时全量读，可能 OOM
    """
    import gc

    files = _glob_files(pattern)
    if not files:
        return pd.DataFrame()

    dfs = []
    for f in files:
        try:
            d = loader_fn(f)
            if d.empty:
                continue

            if sample_per_window is not None and "window_5min" in d.columns:
                sampled = []
                for _, g in d.groupby("window_5min", sort=False):
                    if len(g) > sample_per_window:
                        sampled.append(g.sample(n=sample_per_window, random_state=42))
                    else:
                        sampled.append(g)
                d = pd.concat(sampled, ignore_index=True)
                del sampled
                gc.collect()

            dfs.append(d)
            print(f"[ok] {os.path.basename(f)}: {len(d)} rows")
            del d
            gc.collect()
        except Exception as e:
            print(f"[skip] {f}: {e}")

    if not dfs:
        return pd.DataFrame()
    return pd.concat(dfs, ignore_index=True)


# ==========================================================
# caller / callee 派生
# ==========================================================
def enrich_caller_callee(df: pd.DataFrame) -> pd.DataFrame:
    """
    按 trace_id + parent_id join 父 span 的 service，派生 caller。
    root span 的 caller 设为自己。
    """
    if df.empty:
        return df

    parent = df[["trace_id", "span_id", "service"]].rename(
        columns={"span_id": "parent_id", "service": "caller"}
    )
    df = df.merge(parent, on=["trace_id", "parent_id"], how="left")
    df["callee"] = df["service"]
    df["caller"] = df["caller"].fillna(df["callee"])
    return df


# ==========================================================
# 拓扑构建
# ==========================================================
def build_graph(df: pd.DataFrame) -> nx.DiGraph:
    """按 caller→callee 聚合，边权为平均耗时，附加调用数、失败率。"""
    G = nx.DiGraph()
    if df.empty:
        return G

    edge_df = df.dropna(subset=["caller", "callee"])
    edge_df = edge_df[edge_df["caller"] != edge_df["callee"]]

    for (caller, callee), g in edge_df.groupby(["caller", "callee"]):
        G.add_edge(
            caller, callee,
            weight=float(g["duration_ms"].mean()),
            count=int(len(g)),
            fail_rate=float(g["is_error"].mean()),
        )
    return G


def graph_stats(G: nx.DiGraph) -> pd.DataFrame:
    """输出节点级拓扑统计。"""
    if G.number_of_nodes() == 0:
        return pd.DataFrame(
            columns=["service", "in_degree", "out_degree", "betweenness", "pagerank"]
        )

    bc = nx.betweenness_centrality(G)
    try:
        pr = nx.pagerank(G, weight="weight")
    except Exception:
        pr = {n: 0.0 for n in G.nodes}

    rows = []
    for n in G.nodes:
        rows.append({
            "service": n,
            "in_degree": G.in_degree(n),
            "out_degree": G.out_degree(n),
            "betweenness": bc.get(n, 0.0),
            "pagerank": pr.get(n, 0.0),
        })
    return pd.DataFrame(rows)


# ==========================================================
# 故障传播路径 BFS
# ==========================================================
def trace_bfs(G: nx.DiGraph, df: pd.DataFrame, start_service: str | None = None) -> dict:
    """
    从异常服务出发，双向 BFS 提取传播路径。

    - 向上：沿反向边找 caller，直到没有更多
    - 向下：沿正向边找 callee，直到没有更多
    - 返回：{"upstream": [...], "downstream": [...], "path_edges": [...]}
    """
    if G.number_of_nodes() == 0:
        return {"upstream": [], "downstream": [], "path_edges": []}

    if start_service is None:
        # 用 fail_rate 最高的边的 callee 作为起点
        best_edge = max(
            G.edges(data=True),
            key=lambda e: e[2].get("fail_rate", 0) * e[2].get("count", 1),
            default=None,
        )
        if best_edge is None:
            return {"upstream": [], "downstream": [], "path_edges": []}
        start_service = best_edge[1]

    # 向上
    upstream = []
    queue = [start_service]
    seen = {start_service}
    while queue:
        cur = queue.pop(0)
        for pred in G.predecessors(cur):
            if pred not in seen:
                seen.add(pred)
                upstream.append((pred, cur))
                queue.append(pred)

    # 向下
    downstream = []
    queue = [start_service]
    seen = {start_service}
    while queue:
        cur = queue.pop(0)
        for succ in G.successors(cur):
            if succ not in seen:
                seen.add(succ)
                downstream.append((cur, succ))
                queue.append(succ)

    path_edges = upstream + downstream
    return {
        "start_service": start_service,
        "upstream": upstream,
        "downstream": downstream,
        "path_edges": path_edges,
    }


# ==========================================================
# 窗口聚合
# ==========================================================
def window_features(df: pd.DataFrame) -> pd.DataFrame:
    """按 (window_5min, service) 聚合 span 数与错误数。"""
    cols = ["window_5min", "service", "span_cnt", "error_cnt", "avg_duration_ms", "max_duration_ms"]
    if df.empty:
        return pd.DataFrame(columns=cols)

    out = (
        df.groupby(["window_5min", "service"], as_index=False)
        .agg(
            span_cnt=("span_id", "size"),
            error_cnt=("is_error", "sum"),
            avg_duration_ms=("duration_ms", "mean"),
            max_duration_ms=("duration_ms", "max"),
        )
    )
    return out


# ==========================================================
# CLI
# ==========================================================
def main():
    p = argparse.ArgumentParser(description="Trace loader for AIOps 2025 / GAIA.")
    p.add_argument("--source", required=True, choices=["aiops", "gaia"])
    p.add_argument("--input", required=True, help="glob 模式")
    p.add_argument("--output", required=True, help="spans parquet 路径")
    p.add_argument("--sample-per-window", type=int, default=None,
               help="每个 5min 窗口最多保留多少条 span；推荐 2000")
    p.add_argument("--aggregate", action="store_true", help="输出窗口聚合")
    p.add_argument("--topology", action="store_true", help="输出拓扑节点统计")
    args = p.parse_args()

    if args.source == "aiops":
        df = _load_dir(args.input, load_aiops_traces, sample_per_window=args.sample_per_window)
    else:
        df = _load_dir(args.input, load_gaia_traces, sample_per_window=args.sample_per_window)

    if df.empty:
        print("[warn] empty dataframe; check input pattern:", args.input)
        return

    df = enrich_caller_callee(df)
    df.to_parquet(args.output, index=False)

    print(f"source         : {args.source}")
    print(f"spans          : {len(df)}")
    print(f"traces         : {df['trace_id'].nunique()}")
    print(f"services       : {df['service'].nunique()}")
    print(f"time range     : {df['start_time'].min()} -> {df['start_time'].max()}")
    print(f"missing ts     : {df['start_time'].isna().sum()}")
    print(f"error spans    : {int(df['is_error'].sum())}")
    print(f"cross-service edges : {int((df['caller'] != df['callee']).sum())}")
    print(f"output         : {args.output}")

    if args.aggregate:
        agg = window_features(df)
        agg_path = args.output.replace(".parquet", "_window.parquet")
        agg.to_parquet(agg_path, index=False)
        print(f"window rows    : {len(agg)}")
        print(f"window output  : {agg_path}")

    if args.topology:
        G = build_graph(df)
        stats = graph_stats(G)
        stats_path = args.output.replace(".parquet", "_topology.csv")
        stats.to_csv(stats_path, index=False)
        print(f"graph nodes    : {G.number_of_nodes()}")
        print(f"graph edges    : {G.number_of_edges()}")
        print(f"topology output: {stats_path}")


if __name__ == "__main__":
    main()