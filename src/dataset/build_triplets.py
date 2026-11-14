"""
微调数据集构建：把对齐结果转成 instruction-input-output 三元组。

输入：
    aligned_aiops.parquet  : window_5min, service, log_*, trace_*, metric_*
    groundtruth.jsonl      : 故障标注

输出：
    triplets_aiops.jsonl   : 每行一条 {"instruction", "input", "output", "label", "window_5min"}
"""
from __future__ import annotations

import os
import json
import argparse
import pandas as pd
import numpy as np


INSTRUCTION = (
    "你是一个云计算故障诊断专家。请根据以下系统状态，"
    "按四个阶段输出推理过程：异常识别、关联分析、根因推断、结论总结。"
)

SERVICES_ORDER = [
    "frontend", "cartservice", "checkoutservice", "currencyservice",
    "emailservice", "paymentservice", "productcatalogservice",
    "recommendationservice", "shippingservice", "adservice", "redis",
]


# ==========================================================
# groundtruth 读取与窗口匹配
# ==========================================================
def load_groundtruth(path: str) -> list[dict]:
    gts = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line:
                gts.append(json.loads(line))
    return gts


def match_window(window_ts, gts: list[dict], margin_min: int = 5) -> dict | None:
    """
    判断窗口是否落在某个故障的 [start - margin, end + margin] 内。
    返回匹配的 groundtruth，否则返回 None。
    """
    w = pd.to_datetime(window_ts, utc=True)
    for gt in gts:
        st = pd.to_datetime(gt["start_time"], utc=True)
        et = pd.to_datetime(gt["end_time"], utc=True)
        if st - pd.Timedelta(minutes=margin_min) <= w <= et + pd.Timedelta(minutes=margin_min):
            return gt
    return None


def extract_root_services(gt: dict) -> list[str]:
    """groundtruth 里 service 可能为空、可能是数组，统一提取。"""
    svc = gt.get("service")
    inst = gt.get("instance")

    if svc:
        return [svc] if isinstance(svc, str) else list(svc)
    if isinstance(inst, list):
        return [str(x) for x in inst]
    if inst:
        return [str(inst)]
    return []


# ==========================================================
# 输入文本构建
# ==========================================================
def _fmt(v, ndigits: int = 2):
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return "NaN"
    if isinstance(v, (int, np.integer)):
        return str(int(v))
    if isinstance(v, (float, np.floating)):
        return f"{v:.{ndigits}f}"
    return str(v)


def build_input_text(window_df: pd.DataFrame, window_ts) -> str:
    """
    把该窗口内所有服务的特征转成自然语言。
    window_df: 该窗口下的所有行（每个服务一行）
    """
    lines = []
    lines.append(f"【故障基础信息】")
    lines.append(f"时间窗口：{window_ts}")

    # 涉及服务
    services = sorted(window_df["service"].dropna().unique().tolist())
    lines.append(f"涉及服务：{', '.join(services)}")
    lines.append("")

    # 日志特征
    lines.append(f"【日志异常特征】")
    if "log_log_cnt" in window_df.columns:
        for _, row in window_df.iterrows():
            if row.get("log_log_cnt", 0) > 0:
                lines.append(
                    f"- {row['service']}: "
                    f"log_cnt={_fmt(row.get('log_log_cnt'))}, "
                    f"error_cnt={_fmt(row.get('log_error_cnt'))}, "
                    f"warn_cnt={_fmt(row.get('log_warn_cnt'))}"
                )
        if not any(window_df.get("log_log_cnt", pd.Series([0])) > 0):
            lines.append("- 本窗口无明显异常日志")
    else:
        lines.append("- 日志数据不可用")
    lines.append("")

    # 调用链特征
    lines.append(f"【调用链异常特征】")
    if "trace_span_cnt" in window_df.columns:
        for _, row in window_df.iterrows():
            if pd.notna(row.get("trace_span_cnt")):
                lines.append(
                    f"- {row['service']}: "
                    f"span_cnt={_fmt(row.get('trace_span_cnt'))}, "
                    f"error_span={_fmt(row.get('trace_error_cnt'))}, "
                    f"avg_duration_ms={_fmt(row.get('trace_avg_duration_ms'))}, "
                    f"max_duration_ms={_fmt(row.get('trace_max_duration_ms'))}"
                )
    else:
        lines.append("- 调用链数据不可用")
    lines.append("")

    # 指标特征（只列出关键指标，避免文本过长）
    lines.append(f"【监控指标异常特征】")
    metric_cols = [c for c in window_df.columns if c.startswith("metric_")]
    key_metrics = [
        "metric_rrt", "metric_rrt_max", "metric_error_ratio",
        "metric_client_error_ratio", "metric_server_error_ratio",
        "metric_metric_docker_cpu_usage",
    ]
    shown = [c for c in key_metrics if c in window_df.columns]
    if not shown:
        shown = metric_cols[:10]

    if shown:
        for _, row in window_df.iterrows():
            vals = []
            for c in shown:
                v = row.get(c)
                if pd.notna(v):
                    short = c.replace("metric_", "").replace("metric_docker_", "")
                    vals.append(f"{short}={_fmt(v)}")
            if vals:
                lines.append(f"- {row['service']}: {', '.join(vals)}")
    else:
        lines.append("- 指标数据不可用")

    return "\n".join(lines)


# ==========================================================
# 输出文本构建
# ==========================================================
def build_output_positive(window_df: pd.DataFrame, window_ts, gt: dict) -> str:
    """正样本：四阶段推理 + 根因结论。"""
    root_services = extract_root_services(gt)
    fault_type = gt.get("fault_type", "unknown")
    fault_category = gt.get("fault_category", "unknown")
    instance_type = gt.get("instance_type", "unknown")
    key_obs = gt.get("key_observations", [])
    start_time = gt.get("start_time", "")
    end_time = gt.get("end_time", "")

    # 找证据：该窗口内异常最明显的服务
    top_service = None
    top_log = 0
    if "log_error_cnt" in window_df.columns:
        idx = window_df["log_error_cnt"].fillna(0).idxmax()
        top_service = window_df.loc[idx, "service"]
        top_log = int(window_df.loc[idx, "log_error_cnt"])

    # 阶段1：异常识别
    stage1 = (
        f"{window_ts} 时间窗口内，检测到故障类型 {fault_type}（{fault_category}），"
        f"故障持续时间 {start_time} 至 {end_time}。"
    )
    if top_service and top_log > 0:
        stage1 += f"其中 {top_service} 产生 {top_log} 条错误日志，异常信号最显著。"

    # 阶段2：关联分析
    obs_desc = []
    for obs in key_obs[:3]:
        t = obs.get("type", "")
        kw = obs.get("keyword", [])
        if kw:
            obs_desc.append(f"{t} 维度关键词 {kw}")
    if obs_desc:
        stage2 = "关键观测证据：" + "；".join(obs_desc) + "。"
    else:
        stage2 = "通过日志、指标、调用链三源数据交叉验证异常传播路径。"

    # 阶段3：根因推断
    stage3 = (
        f"根因服务为 {', '.join(root_services) if root_services else 'unknown'}，"
        f"故障类型为 {fault_type}，故障层级为 {instance_type}。"
    )

    # 阶段4：结论总结
    stage4 = (
        f"建议排查 {', '.join(root_services)} 的 {fault_type} 相关配置，"
        f"并结合调用链上下游服务进行故障隔离与恢复验证。"
    )

    return (
        f"阶段1（异常识别）：{stage1}\n"
        f"阶段2（关联分析）：{stage2}\n"
        f"阶段3（根因推断）：{stage3}\n"
        f"阶段4（结论总结）：{stage4}"
    )


def build_output_negative(window_df: pd.DataFrame, window_ts) -> str:
    """负样本：系统正常。"""
    max_log = 0
    if "log_error_cnt" in window_df.columns:
        max_log = int(window_df["log_error_cnt"].fillna(0).max())

    return (
        f"阶段1（异常识别）：{window_ts} 时间窗口内，各服务日志错误数最大为 {max_log}，"
        f"未检测到显著异常信号。\n"
        f"阶段2（关联分析）：调用链指标和监控指标均在正常范围内波动。\n"
        f"阶段3（根因推断）：无根因，系统运行正常。\n"
        f"阶段4（结论总结）：系统当前状态健康，无需干预。"
    )


# ==========================================================
# 主逻辑
# ==========================================================
def build_triplets(aligned_path: str, gt_path: str) -> list[dict]:
    df = pd.read_parquet(aligned_path)
    gts = load_groundtruth(gt_path)

    print(f"aligned rows: {len(df)}, windows: {df['window_5min'].nunique()}, groundtruth: {len(gts)}")

    triplets = []
    windows = sorted(df["window_5min"].dropna().unique())

    for w in windows:
        w_df = df[df["window_5min"] == w]
        if w_df.empty:
            continue

        gt = match_window(w, gts)
        input_text = build_input_text(w_df, w)

        if gt is not None:
            output_text = build_output_positive(w_df, w, gt)
            label = "positive"
            fault_type = gt.get("fault_type", "")
            root_services = extract_root_services(gt)
        else:
            output_text = build_output_negative(w_df, w)
            label = "negative"
            fault_type = ""
            root_services = []

        triplets.append({
            "instruction": INSTRUCTION,
            "input": input_text,
            "output": output_text,
            "label": label,
            "window_5min": str(w),
            "fault_type": fault_type,
            "root_services": root_services,
        })

    return triplets


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--aligned", required=True, help="aligned_aiops.parquet")
    p.add_argument("--groundtruth", required=True, help="groundtruth.jsonl")
    p.add_argument("--output", required=True, help="输出 jsonl")
    args = p.parse_args()

    triplets = build_triplets(args.aligned, args.groundtruth)

    pos = sum(1 for t in triplets if t["label"] == "positive")
    neg = sum(1 for t in triplets if t["label"] == "negative")
    print(f"\ntriplets: {len(triplets)} (positive {pos}, negative {neg})")

    # 输入长度统计
    input_lens = [len(t["input"]) for t in triplets]
    output_lens = [len(t["output"]) for t in triplets]
    print(f"input length:  min={min(input_lens)}, max={max(input_lens)}, mean={sum(input_lens)//len(input_lens)}")
    print(f"output length: min={min(output_lens)}, max={max(output_lens)}, mean={sum(output_lens)//len(output_lens)}")

    with open(args.output, "w", encoding="utf-8") as f:
        for t in triplets:
            f.write(json.dumps(t, ensure_ascii=False) + "\n")
    print(f"\noutput: {args.output}")


if __name__ == "__main__":
    main()