import glob
import os
import json
import pandas as pd


def find_parquet(pattern):
    files = glob.glob(pattern, recursive=True)
    return [f for f in files if not os.path.basename(f).startswith("._")]


def explore_parquet(pattern, n=5):
    files = find_parquet(pattern)
    print("\n" + "=" * 80)
    print(f"### {pattern} | files: {len(files)}")
    if not files:
        print("no files found")
        return
    path = files[0]
    print("file:", path)
    df = pd.read_parquet(path).head(n)
    print("shape:", df.shape)
    print("columns:", list(df.columns))
    print(df.to_string())


if __name__ == "__main__":
    base = "data/raw/aiops2025/2025-06-06/metric-parquet"

    # 日志：抽一个文件代表
    explore_parquet("data/raw/aiops2025/2025-06-06/log-parquet/*.parquet")

    # 指标：按子目录分别看
    explore_parquet(os.path.join(base, "apm/service/*.parquet"))
    explore_parquet(os.path.join(base, "apm/pod/*.parquet"))
    explore_parquet(os.path.join(base, "infra/infra_node/*.parquet"))
    explore_parquet(os.path.join(base, "infra/infra_pod/*.parquet"))
    explore_parquet(os.path.join(base, "infra/infra_tidb/*.parquet"))
    explore_parquet(os.path.join(base, "other/*.parquet"))

    # 调用链：抽一个文件代表
    explore_parquet("data/raw/aiops2025/2025-06-06/trace-parquet/*.parquet")

    # groundtruth 前 3 条
    print("\n" + "=" * 80)
    print("### AIOps 2025 groundtruth.jsonl")
    with open("data/raw/aiops2025/groundtruth.jsonl") as f:
        for i, line in enumerate(f):
            if i >= 3:
                break
            print(json.dumps(json.loads(line), ensure_ascii=False, indent=2)[:800])