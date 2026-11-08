"""
日志加载与解析模块。

支持三种日志源：
1. AIOps 2025 log-parquet（Filebeat 采集，需多层清洗）
2. GAIA MicroSS/business（结构化 message，含 trace_id）
3. GAIA MicroSS/run（故障注入记录，需正则解析）

统一输出 schema：
    timestamp      : datetime64[ns, UTC]
    service        : str
    pod            : str | None
    node           : str | None
    level          : INFO / WARN / ERROR / DEBUG / FATAL / UNKNOWN
    message        : str  (原始 message)
    message_clean  : str  (ANSI 清洗后)
    trace_id       : str | None
    window_5min    : datetime64[ns, UTC]
"""
from __future__ import annotations

import re
import os
import glob
import json
import argparse
import pandas as pd


# ---------------- 共享常量 ----------------
ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")
LEVEL_PATTERN = re.compile(
    r"\b(ERROR|WARN|WARNING|INFO|DEBUG|FATAL|TRACE)\b", re.IGNORECASE
)
HTTP_STATUS_PATTERN = re.compile(r"\s(\d{3})\s")
ERROR_LEVELS = {"ERROR", "WARN", "WARNING"}


def clean_message(msg) -> str:
    """去掉 ANSI 转义码，None/NaN 返回空字符串。"""
    if pd.isna(msg):
        return ""
    return ANSI_RE.sub("", str(msg))


# ==========================================================
# AIOps 2025 日志
# ==========================================================
def extract_level_aiops(msg) -> str:
    """三层级别提取：JSON severity → ANSI 清洗后正则 → HTTP 状态码推断。"""
    if pd.isna(msg):
        return "UNKNOWN"
    raw = str(msg)

    # 1) JSON
    s = raw.strip()
    if s.startswith("{"):
        try:
            obj = json.loads(s)
            sev = obj.get("severity") or obj.get("level") or obj.get("log.level")
            if sev:
                sev = str(sev).upper()
                return "WARN" if sev == "WARNING" else sev
        except (json.JSONDecodeError, TypeError):
            pass

    # 2) 去 ANSI 后正则
    cleaned = clean_message(raw)
    m = LEVEL_PATTERN.search(cleaned)
    if m:
        lv = m.group(1).upper()
        return "WARN" if lv == "WARNING" else lv

    # 3) HTTP 状态码
    m = HTTP_STATUS_PATTERN.search(cleaned)
    if m:
        code = int(m.group(1))
        if code >= 500:
            return "ERROR"
        if code >= 400:
            return "WARN"

    return "UNKNOWN"


def pod_to_service(pod):
    """'cartservice-0' → 'cartservice'；None 返回 None。"""
    if pd.isna(pod):
        return None
    parts = str(pod).rsplit("-", 1)
    if len(parts) == 2 and parts[1].isdigit():
        return parts[0]
    return str(pod)


def load_aiops_logs(path: str) -> pd.DataFrame:
    """读单个 AIOps 2025 log-parquet 文件，输出统一 schema。"""
    df = pd.read_parquet(path)
    df = df.rename(
        columns={
            "@timestamp": "timestamp",
            "k8_node_name": "node",
            "k8_pod": "pod",
        }
    )
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True, errors="coerce")
    df["service"] = df["pod"].apply(pod_to_service)
    df["level"] = df["message"].apply(extract_level_aiops)
    df["message_clean"] = df["message"].apply(clean_message)
    df["trace_id"] = None
    df["window_5min"] = df["timestamp"].dt.floor("5min")

    keep = [
        "timestamp", "service", "pod", "node", "level",
        "message", "message_clean", "trace_id", "window_5min",
    ]
    return df[keep]


# ==========================================================
# GAIA business 日志
# ==========================================================
GAIA_MSG_SEP = " | "


def parse_gaia_business_message(msg) -> dict:
    """
    解析 7 段式 message：
        time | level | ip | service | code_location | trace_id | body
    不足 6 段时走 fallback。
    """
    default = {
        "log_time": None,
        "level": "UNKNOWN",
        "host_ip": None,
        "service_msg": None,
        "code_location": None,
        "trace_id": None,
        "message_body": "" if pd.isna(msg) else str(msg),
    }
    if pd.isna(msg):
        return default

    parts = str(msg).split(GAIA_MSG_SEP)
    if len(parts) < 6:
        return default

    return {
        "log_time": parts[0].strip() or None,
        "level": (parts[1].strip().upper() or "UNKNOWN"),
        "host_ip": parts[2].strip() or None,
        "service_msg": parts[3].strip() or None,
        "code_location": parts[4].strip() or None,
        "trace_id": parts[5].strip() or None,
        "message_body": GAIA_MSG_SEP.join(parts[6:]) if len(parts) > 6 else "",
    }


def load_gaia_business(path: str) -> pd.DataFrame:
    """读单个 GAIA business_table_*.csv。"""
    df = pd.read_csv(path)
    if "id" in df.columns:
        df = df.drop(columns=["id"])

    parsed = df["message"].apply(parse_gaia_business_message).apply(pd.Series)
    df = pd.concat([df.drop(columns=["message"]), parsed], axis=1)

    # log_time 形如 "2021-08-01 00:00:01,302"；把逗号换成点再解析
    df["timestamp"] = pd.to_datetime(
        df["log_time"].astype(str).str.replace(",", ".", regex=False),
        format="%Y-%m-%d %H:%M:%S.%f",
        utc=True,
        errors="coerce",
    )
    df["service"] = df["service"].fillna(df["service_msg"])
    df["pod"] = None
    df["node"] = None
    df["message_clean"] = df["message_body"].apply(clean_message)
    df["window_5min"] = df["timestamp"].dt.floor("5min")

    df = df.rename(columns={"message_body": "message"})
    keep = [
        "timestamp", "service", "pod", "node", "level",
        "message", "message_clean", "trace_id", "window_5min",
    ]
    return df[keep]


# ==========================================================
# GAIA run（故障注入记录）
# ==========================================================
GAIA_RUN_PATTERNS = {
    "fault_type": re.compile(r"\[(.*?)\]"),
    "start_time": re.compile(r"start at (.+?) and"),
    "duration_s": re.compile(r"lasts (\d+)\s+seconds"),
    "memory_used": re.compile(r"use (\d+\w+) memory"),
    "level": re.compile(r"\|\s*(WARNING|ERROR|INFO)\s*\|"),
}


def parse_gaia_run_message(msg) -> dict:
    if pd.isna(msg):
        return {}
    s = str(msg)
    out = {}
    for field, pat in GAIA_RUN_PATTERNS.items():
        m = pat.search(s)
        out[field] = m.group(1).strip() if m else None
    return out


def load_gaia_run(path: str) -> pd.DataFrame:
    """读单个 run_table_*.csv；故障注入记录。"""
    df = pd.read_csv(path)
    parsed = df["message"].apply(parse_gaia_run_message).apply(pd.Series)
    df = pd.concat([df, parsed], axis=1)

    df["timestamp"] = pd.to_datetime(df["datetime"], utc=True, errors="coerce")
    df["pod"] = None
    df["node"] = None
    df["message_clean"] = df["message"].apply(clean_message)
    df["trace_id"] = None
    df["window_5min"] = df["timestamp"].dt.floor("5min")
    if "level" not in df.columns:
        df["level"] = "UNKNOWN"
    df["level"] = df["level"].fillna("UNKNOWN")

    keep = [
        "timestamp", "service", "pod", "node", "level",
        "message", "message_clean", "trace_id", "window_5min",
        "fault_type", "start_time", "duration_s", "memory_used",
    ]
    return df[[c for c in keep if c in df.columns]]


# ==========================================================
# 工具函数：glob 跳过 macOS 元数据文件
# ==========================================================
def _glob_files(pattern: str) -> list[str]:
    files = glob.glob(pattern, recursive=True)
    return [f for f in files if not os.path.basename(f).startswith("._")]


def _load_dir(pattern: str, loader_fn) -> pd.DataFrame:
    files = _glob_files(pattern)
    if not files:
        return pd.DataFrame()
    dfs = []
    for f in files:
        try:
            dfs.append(loader_fn(f))
        except Exception as e:
            print(f"[skip] {f}: {e}")
    return pd.concat(dfs, ignore_index=True) if dfs else pd.DataFrame()


# ==========================================================
# 统一窗口聚合
# ==========================================================
def window_features(df: pd.DataFrame) -> pd.DataFrame:
    """按 (window_5min, service) 聚合 ERROR/WARN 日志。"""
    cols = ["window_5min", "service", "log_cnt", "error_cnt", "warn_cnt"]
    if df.empty or "level" not in df.columns:
        return pd.DataFrame(columns=cols)

    err = df[df["level"].isin(ERROR_LEVELS)].copy()
    if err.empty:
        return pd.DataFrame(columns=cols)

    err["is_error"] = (err["level"] == "ERROR").astype(int)
    err["is_warn"] = err["level"].isin(["WARN", "WARNING"]).astype(int)

    out = (
        err.groupby(["window_5min", "service"], as_index=False)
        .agg(
            log_cnt=("is_error", "size"),
            error_cnt=("is_error", "sum"),
            warn_cnt=("is_warn", "sum"),
        )
    )
    return out


# ==========================================================
# CLI
# ==========================================================
def main():
    p = argparse.ArgumentParser(description="Log loader for AIOps 2025 / GAIA.")
    p.add_argument(
        "--source", required=True,
        choices=["aiops", "gaia_business", "gaia_run"],
    )
    p.add_argument(
        "--input", required=True,
        help="glob 模式，例如 'data/raw/.../log-parquet/*.parquet'",
    )
    p.add_argument("--output", required=True, help="输出 parquet 路径")
    p.add_argument("--aggregate", action="store_true", help="同时输出窗口聚合")
    args = p.parse_args()

    if args.source == "aiops":
        df = _load_dir(args.input, load_aiops_logs)
    elif args.source == "gaia_business":
        df = _load_dir(args.input, load_gaia_business)
    else:
        df = _load_dir(args.input, load_gaia_run)

    if df.empty:
        print("[warn] empty dataframe; check input pattern:", args.input)
        return

    df.to_parquet(args.output, index=False)

    print(f"source         : {args.source}")
    print(f"rows           : {len(df)}")
    print(f"services       : {df['service'].nunique()}")
    print(f"time range     : {df['timestamp'].min()} -> {df['timestamp'].max()}")
    print(f"levels         : {df['level'].value_counts().to_dict()}")
    print(f"missing ts     : {df['timestamp'].isna().sum()}")
    print(f"output         : {args.output}")

    if args.aggregate:
        agg = window_features(df)
        agg_path = args.output.replace(".parquet", "_window.parquet")
        agg.to_parquet(agg_path, index=False)
        print(f"window rows    : {len(agg)}")
        print(f"window output  : {agg_path}")


if __name__ == "__main__":
    main()