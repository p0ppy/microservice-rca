# 字段映射表

## 目录

1. AIOps 2025 日志
2. AIOps 2025 调用链
3. AIOps 2025 指标：apm/service 与 apm/pod（宽表）
4. AIOps 2025 指标：infra/*（窄表）
5. AIOps 2025 故障标注：groundtruth.jsonl
6. GAIA
   - 6.1 GAIA 数据源说明
   - 6.2 GAIA 故障注入记录（run_table）
   - 6.3 GAIA 业务日志（MicroSS/business）
   - 6.4 GAIA 指标（MicroSS/metric）
   - 6.5 GAIA 调用链（MicroSS/trace）
7. RCA100
   - 7.1 RCA100 调用链（trace）
   - 7.2 待补

---

### 1. AIOps 2025 日志

| 原始字段 | 含义 | 标准化字段 | 示例 / 说明 |
|---|---|---|---|
| @timestamp | 日志采集时间（UTC） | timestamp | 2025-06-05T19:00:00.221Z；Z 表示 UTC |
| k8_namespace | K8s 命名空间 | namespace | hipstershop |
| agent_name | Filebeat agent 名 | agent | filebeat-filebeat-hdb2p；辅助字段 |
| k8_pod | Pod 名称 | pod | cartservice-0；StatefulSet 格式 `<service>-<ordinal>` |
| k8_node_name | 节点名称 | node | aiops-k8s-07 |
| message | 日志正文 | message | 含 ANSI、JSON、纯文本三种格式 |
| （派生）k8_pod | 服务名 | service | 去掉末尾 `-数字`：cartservice-0 → cartservice |
| （派生）message | 日志级别 | level | JSON severity → ANSI 清洗后正则 → HTTP 状态码推断 |
| （缺失）— | 调用链 ID | trace_id | 日志无此字段；AIOps 2025 日志与 trace 采用窗口 + service + node 弱关联 |

### 关键说明

- `@timestamp` 是 ISO 8601 UTC 字符串，解析用 `pd.to_datetime(df["@timestamp"], utc=True)`。
- `k8_pod` 是 `<service>-<ordinal>`，`service` 用 `rsplit("-", 1)` 去掉末尾数字。
- `level` 三层提取：
  1. message 是 JSON 时取 `severity` / `level`；
  2. 去掉 ANSI 转义码后用正则匹配 `ERROR|WARN|WARNING|INFO|DEBUG|FATAL`；
  3. 纯文本 HTTP 访问日志按状态码推断：5xx → ERROR，4xx → WARN，2xx/3xx → UNKNOWN。
- 正常访问日志可能没有级别，会被标为 `UNKNOWN`，过滤 ERROR/WARN 时被丢弃，这是预期行为。
- `window_5min = timestamp.dt.floor("5min")`。

## 2. AIOps 2025 调用链

| 原始字段 | 含义 | 标准化字段 | 示例 / 说明 |
|---|---|---|---|
| traceID | 调用链 ID | trace_id | 32 位十六进制 |
| spanID | 当前 span ID | span_id | 16 位十六进制 |
| references[refType=CHILD_OF].spanID | 父 span ID | parent_id | root span 为空 |
| process.serviceName | 服务名 | service | frontend |
| process.tags[key=name].value | Pod 名 | pod | frontend-1 |
| process.tags[key=node_name].value | 节点名 | node | aiops-k8s-07 |
| startTime | 开始时间（微秒） | start_time_us | 1749222060344762 |
| startTimeMillis | 开始时间（毫秒） | start_time_ms | 1749222060344 |
| duration | 持续时间（微秒） | duration_us | 19416 ≈ 19.4ms |
| tags[key=status.code].value | 状态码 | status | 0=OK，非 0=ERROR |
| tags[key=span.kind].value | span 类型 | span_kind | client / server |
| tags[key=net.peer.ip].value | 对端服务 | peer_service | 值是服务名，不是 IP |
| operationName | 操作名 | operation | hipstershop.ProductCatalogService/GetProduct |
| logs | span 事件 | — | 可忽略 |
| flags | 采样标志 | — | 可忽略 |

### 派生字段

| 派生字段 | 方式 | 说明 |
|---|---|---|
| caller | 按 `trace_id + parent_id` join 父 span 的 `service` | root span 的 caller 可设为自身或空 |
| callee | 当前 span 的 `service` | 被调用方 |
| duration_ms | `duration_us / 1000` | 统一毫秒 |

### 关键说明

- 没有现成 `caller` / `callee`，必须从 `references` 的 `CHILD_OF` 派生。
- `duration` 单位微秒，转毫秒除以 1000。
- 同一对 caller/callee 在一个 trace 中可出现多次，聚合边权时用 `groupby(["caller","callee"]).agg(mean, count)`。
- `process` 是嵌套字典，服务名、Pod、节点都要从里面抽。
- `startTime` 与日志 `@timestamp` 均为 UTC。

## 3. AIOps 2025 指标：apm/service 与 apm/pod（宽表）

| 原始字段 | 含义 | 标准化字段 | 示例 / 说明 |
|---|---|---|---|
| time | 时间（UTC，分钟级） | timestamp | 2025-06-05T16:00:00Z；ISO 8601 字符串 |
| object_id | 实体 ID | entity | apm/service 下是服务名；apm/pod 下是 Pod 名 |
| object_type | 实体类型 | entity_type | service / pod |
| request | 请求量 | request | 计数 |
| response | 响应量 | response | 计数 |
| rrt | 平均响应时间 | rrt | 对应 groundtruth 的 rrt |
| rrt_max | 最大响应时间 | rrt_max | 对应 groundtruth 的 rrt_max |
| error | 错误数 | error | 计数 |
| error_ratio | 错误率 | error_ratio | 比例 |
| client_error | 客户端错误数 | client_error | 对应 groundtruth |
| client_error_ratio | 客户端错误率 | client_error_ratio | 对应 groundtruth |
| server_error | 服务端错误数 | server_error | 计数 |
| server_error_ratio | 服务端错误率 | server_error_ratio | 比例 |
| timeout | 超时数 | timeout | 计数 |

### 关键说明

- 每个文件是一个实体一整天的数据，一分钟一行。
- `apm/service` 下约 11 个服务，`apm/pod` 下约 30 个 Pod。
- 一个实体一个时间点只有一行，不需要按维度聚合。
- `timestamp` 直接 `pd.to_datetime(df["time"], utc=True)`。
- `groundtruth.key_observations` 里的 `rrt`、`rrt_max`、`client_error`、`client_error_ratio` 可直接对应这里的列名。

## 4. AIOps 2025 指标：infra/*（窄表）

| 原始字段 | 含义 | 标准化字段 | 示例 / 说明 |
|---|---|---|---|
| time | 时间（UTC，分钟级） | timestamp | 2025-06-05T16:00:00Z；同一时刻多行 |
| kpi_key | 英文指标名 | metric | pod_fs_reads_bytes；与值列名一致，可信 |
| kpi_name | 中文描述 | metric_cn | 累计读取字节数；仅展示，不参与逻辑 |
| `<kpi_key 同名列>` | 指标值 | value | 列名随文件变化，用排除法找 |
| pod | Pod 名 | entity（infra_pod） | 实体列 |
| kubernetes_node | 节点名 | entity（infra_node） / node | 实体或节点 |
| instance | 实例 | instance | 节点或实例标识 |
| namespace | 命名空间 | namespace | hipstershop |
| object_type | 实体类型 | entity_type | node / pod / tidb |
| device | 设备 | device | 维度列 |
| mountpoint | 挂载点 | mountpoint | 维度列 |
| type | 类型 | type | 维度列 |
| cf | 列族 | cf | 仅 TiDB |
| sql_type | SQL 类型 | sql_type | 仅 TiDB |

### 关键说明

- 值列名 = `kpi_key` = 英文指标名（如 `pod_fs_reads_bytes`）。
- `kpi_name` 是中文描述（如 `累计读取字节数`），**不是列名**。
- 找值列用排除法：不在下面元数据列里的那一列就是值列：
  `time, cf, device, instance, kpi_key, kpi_name, kubernetes_node, mountpoint, namespace, object_type, pod, sql_type, type`。
- 同一 `timestamp` 有多行，原因是 `device`、`mountpoint`、`type` 等维度不同。
- 聚合方式：按 `timestamp + entity + metric` 聚合；比率型用 `mean`，累加型用 `sum`。
- 实体选择：`infra_pod` 用 `pod`；`infra_node` 用 `kubernetes_node`；兜底用 `instance`。
- 与 `groundtruth.key_metrics` 匹配时用 `kpi_key`，不要用 `kpi_name`。

## 5.AIOps 2025 故障标注（groundtruth.jsonl）

| 原始字段 | 含义 | 标准化字段 | 示例 / 用途 |
|---|---|---|---|
| uuid | 案例唯一 ID | sample_id | 345fbe93-80；三元组主键 |
| fault_category | 故障大类 | fault_category | stress test / network attack / pod fault；输出标签 |
| fault_type | 故障类型 | fault_type | cpu stress / network corrupt；输出标签 |
| instance_type | 故障层级 | instance_type | service / pod / node；输出标签 |
| service | 根因服务 | root_service | emailservice；可能为空，为空时用 instance 兜底 |
| instance | 根因实例 | root_instance | 字符串或数组；数组时 join 成文本 |
| source | 网络故障源 | source | checkoutservice；仅 network 类有值 |
| destination | 网络故障目标 | destination | shippingservice；仅 network 类有值 |
| start_time | 故障开始（UTC，秒级） | start_time | 2025-06-05T16:10:02Z；窗口过滤起点 |
| end_time | 故障结束（UTC，秒级） | end_time | 2025-06-05T16:31:02Z；窗口过滤终点 |
| key_observations | 证据检查点 | key_observations | list[{type, keyword}]；证据筛选器 |
| key_metrics | 关键指标名 | key_metrics | ["pod_cpu_usage"]；定位 infra 文件 |
| fault_description | 故障描述同义词 | fault_description | 文本增强；RAG 知识库关键词 |

### 特殊处理
- service 为空时，用 instance 的第一个元素作为根因服务。
- instance 是数组时，用 " / " 连接。
- key_observations 的 type 为 metric / log / trace，分别去对应数据源找证据。
- 时间精度秒级，metric 是分钟级，窗口过滤用 floor/ceil 到分钟。

## 6.GAIA

### 6.1 GAIA 数据源说明

| 位置 | 内容 | 用途 |
|---|---|---|
| MicroSS/business/ | 业务日志，字段 `datetime, service, message`；message 含级别、trace_id | 多源对齐的日志源之一 |
| MicroSS/run/ | 故障注入记录 / 系统事件，字段 `datetime, service, message` | GAIA 的故障标注来源 |
| MicroSS/metric/ | 指标，一指标一文件，`timestamp, value` | 指标源 |
| MicroSS/trace/ | 调用链，扁平表 | 调用链源 |
| Companion_Data/log.zip | Loghub 日志解析基准（kafka/hadoop/zookeeper 等） | 仅用于验证 DRAIN3 等日志模板挖掘，**不是 MicroSS 运行日志** |
| Companion_Data/metric_detection.zip | 指标异常检测辅助结果 | 辅助，不作为主数据源 |
| Companion_Data/metric_forecast.zip | 指标预测辅助结果 | 辅助，不作为主数据源 |

**结论：GAIA 在 metric + trace 之外，`business` 提供业务日志、`run` 提供故障注入记录，可做三源对齐；`log.zip` 不作为 MicroSS 日志源。**

> GAIA 的 CSV 字符串时间（business、run、trace）**没有带时区**；metric 的 `timestamp` 是 Unix 毫秒绝对时间。对齐前必须确认字符串时间的时区，并统一转换，不要默认按 UTC。

### 6.2 GAIA 故障注入记录（run_table）

| 原始字段 | 含义 | 标准化字段 | 示例 / 说明 |
|---|---|---|---|
| datetime | 日期 | date | 2021-07-01；仅日期 |
| service | 服务名 | service | dbservice1 |
| message | 故障描述 | fault_message | 自然语言，含故障类型、开始时间、时长、资源量 |

#### message 正则提取规则

| 提取项 | 正则 | 示例 |
|---|---|---|
| 故障类型 | `\[(.*?)\]` | memory_anomalies |
| 开始时间 | `start at (.+?) and` | 2021-07-01 11:44:26.882752 |
| 持续时长 | `lasts (\d+) seconds` | 600 |
| 资源量 | `use (\d+\w+) memory` | 1g |
| 严重级别 | `\| (WARNING\|ERROR\|INFO) \|` | WARNING |

#### 关键说明

- GAIA `run` 表是半结构化，需正则解析 `message`。
- 无 `key_observations`，证据需从日志/指标中自行提取。
- 时间只有日期级，精确开始时间在 `message` 内。
- `run` 表同时承担“故障注入记录”和“故障标注”角色。
- `message` 样例：
  ```text
  2021-07-01 11:54:27,616 | WARNING | 0.0.0.4 | 172.17.0.3 | dbservice1 | [memory_anomalies] trigger a high memory program, start at 2021-07-01 11:44:26.882752 and lasts 600 seconds and use 1g memory

### 6.3 GAIA 业务日志（MicroSS/business）

| 原始字段 | 含义 | 标准化字段 | 示例 / 说明 |
|---|---|---|---|
| id | 行号 | — | 仅部分文件有；读取时按列名判断 |
| datetime | 日期 | date | 2021-08-01；仅日期 |
| service | 服务名 | service | dbservice2；与 message 第 4 段一致 |
| message | 结构化日志 | message | 按 ` \| ` 切分为 7 段 |

#### message 解析规则

格式：

```text
时间 | 级别 | IP | 服务名 | 代码位置 | trace_id | 业务描述


### 6.4 GAIA 指标（MicroSS/metric）

| 原始字段 | 含义 | 标准化字段 | 示例 / 说明 |
|---|---|---|---|
| timestamp | Unix 毫秒时间戳 | timestamp | 1627747202000；样例间隔 30 秒 |
| value | 指标值 | value | 6488064 |
| 文件名 | 实体 + 指标名 | entity, metric_raw | 用正则拆分 |

#### 文件名解析

格式：`<实体>_<IP>_<指标名>_<开始日期>_<结束日期>.csv`

正则：

```text
^(?P<entity>.+?_\d+\.\d+\.\d+\.\d+)_(?P<metric>.+)_(?P<start>\d{4}-\d{2}-\d{2})_(?P<end>\d{4}-\d{2}-\d{2})\.csv$
```

示例：

```text
dbservice1_0.0.0.4_docker_memory_stats_mapped_file_2021-08-01_2021-08-31.csv
→ entity = dbservice1_0.0.0.4
→ metric = docker_memory_stats_mapped_file
```

#### 指标规模

- 文件总数：10817。
- 去重后约 591 个指标名。
- 前缀分布：system 385、docker 90、redis 89、zookeeper 27。

#### CPU 聚合

- `docker_cpu_core_{0..23}_ticks`：1008 个文件
- `docker_cpu_core_{0..23}_pct`：1008 个文件
- `docker_cpu_core_{0..23}_norm_pct`：1008 个文件
- 合计 3024 个，占 28%。

策略：只保留 `_pct`，24 核取 `mean`，聚合为 `docker_cpu_usage`；丢弃 `_ticks` 和 `_norm_pct`。

#### system 保留 16 个

| 子类 | 保留 | 数量 |
|---|---|---|
| CPU | cpu_user_pct, cpu_system_pct, cpu_iowait_pct, cpu_idle_pct | 4 |
| 负载 | load_norm_1, load_norm_5, load_norm_15 | 3 |
| 磁盘 | diskio_read_bytes, diskio_write_bytes | 2 |
| 文件系统 | fsstat_total_size_used, fsstat_total_size_free | 2 |
| 内存 | memory_used_bytes, memory_used_pct | 2 |
| 网络 | network_summary_tcp_CurrEstab, network_summary_tcp_EstabResets, network_summary_tcp_ListenDrops | 3 |

`system_network_summary` 约 60-80 个指标名、2281 个文件，只留上述 3 个，其余 ICMP/UDP/IP 计数器全部砍掉。

#### docker 保留约 14 个

- CPU：`docker_cpu_usage`（聚合后）
- 内存：`docker_memory_usage_pct`、`docker_memory_usage_total`、`docker_memory_fail_count`
- 磁盘：`docker_diskio_read_bytes`、`docker_diskio_write_bytes`、`docker_diskio_write_rate`、`docker_diskio_write_queued`
- 网络：`docker_network_in_bytes`、`docker_network_out_bytes`、`docker_network_in_errors`、`docker_network_out_errors`、`docker_network_in_dropped`、`docker_network_out_dropped`

#### redis / zookeeper

- redis：保留连接数、命中率、内存、延迟类 5-8 个。
- zookeeper：保留连接数、延迟、节点数类 3-5 个。

#### 指标名归一化

```python
def normalize_metric_name(name):
    if name.startswith("docker_cpu_core_"):
        if name.endswith("_norm_pct") or name.endswith("_pct"):
            return "docker_cpu_usage"
        if name.endswith("_ticks"):
            return "docker_cpu_ticks_total"
    return name
```

#### 聚合方式

| 类型 | 聚合 | 例子 |
|---|---|---|
| 比率型 `_pct` / `_ratio` | mean 或 max | docker_cpu_usage |
| 累加型 `_ticks` / `_bytes_total` | sum 后一阶差分 | docker_cpu_ticks_total |
| 绝对量 `_bytes` / `_count` | mean | docker_network_in_bytes |

#### 读取注意

- `timestamp` 是 Unix 毫秒，`pd.to_datetime(df["timestamp"], unit="ms", utc=True)`。
- 样例采样间隔 30 秒，对齐时需重采样到统一频率。
- 指标名和实体只在文件名里，读文件时必须同时解析文件名。
- 聚合后每实体每分钟一行。
- 最终目标约 35-50 个核心指标，对应开题“保留 50-80 个”的目标。

---

### 6.5 GAIA 调用链（MicroSS/trace）

| 原始字段 | 含义 | 标准化字段 | 示例 / 说明 |
|---|---|---|---|
| timestamp | 日志时间（秒级） | timestamp | 2021-07-01 09:57:04 |
| host_ip | 主机 IP | host_ip | 0.0.0.1 |
| service_name | 服务名 | service | webservice1 |
| trace_id | 调用链 ID | trace_id | 22741016938865b0 |
| span_id | span ID | span_id | 3a98d34fb3b6075e |
| parent_id | 父 span ID | parent_id | 0 表示 root；直接给出 |
| start_time | 开始时间（微秒） | start_time | 2021-07-01 09:57:04.255416 |
| end_time | 结束时间（微秒） | end_time | 2021-07-01 09:57:04.268775 |
| url | 请求 URL | url | http://0.0.0.1:9379/web_login_service |
| status_code | HTTP 状态码 | status | 500 / 200 / 300 |
| message | 调用描述 | message | request call function 1 ... |

#### 派生字段

| 派生 | 方式 | 说明 |
|---|---|---|
| duration_us | `end_time - start_time` | 单位微秒 |
| caller | 按 `trace_id + parent_id` join 父 span 的 `service` | root 的 caller 设为自身 |
| callee | 当前 span 的 `service` | 被调用方 |

#### 关键说明

- `parent_id` 直接给出，root 为 `0`，不需要从嵌套结构解析。
- `status_code` 是 HTTP 状态码，异常判定用 `status_code >= 400`。
- `start_time` / `end_time` 是微秒级字符串，`duration` 要自己算。
- 文件按服务 + 月份切分：`trace_table_<服务>_<YYYY-MM>.csv`。
- 覆盖 10 个服务：dbservice1/2、logservice1/2、mobservice1/2、redisservice1/2、webservice1/2。
- 没有 `caller` / `callee` 字段，需按 `parent_id` join 派生。
- 时间字符串无时区，对齐前需确认时区。

#### 与 AIOps 2025 trace 的差异

| 维度 | AIOps 2025 | GAIA |
|---|---|---|
| 结构 | Jaeger 嵌套 | 扁平表，11 列 |
| parent_id | references[CHILD_OF] | parent_id 直接给出，root=0 |
| 服务名 | process.serviceName | service_name |
| 状态 | tags[status.code]，0=OK | status_code，HTTP 码，>=400 异常 |
| 时间单位 | startTime 微秒 int | start_time 微秒字符串 |
| duration | duration 微秒 int | end_time - start_time |
| 文件组织 | 一文件含所有 span | 一服务一月一文件 |
| trace_id 关联 | 日志无 trace_id，弱关联 | business 日志有 trace_id，强关联 |

#### caller / callee 派生

```python
parent = df[["trace_id", "span_id", "service"]].rename(
    columns={"span_id": "parent_id", "service": "caller"}
)
df = df.merge(parent, on=["trace_id", "parent_id"], how="left")
df["callee"] = df["service"]
df["caller"] = df["caller"].fillna(df["callee"])
```

#### duration 计算

```python
df["start_time"] = pd.to_datetime(df["start_time"])
df["end_time"] = pd.to_datetime(df["end_time"])
df["duration_us"] = (df["end_time"] - df["start_time"]).dt.total_seconds() * 1e6
df["duration_ms"] = df["duration_us"] / 1000
```

#### 异常判定与拓扑

```python
df["is_error"] = df["status_code"].astype(int) >= 400

import networkx as nx
G = nx.DiGraph()
for (caller, callee), g in df.dropna(subset=["caller","callee"]).groupby(["caller","callee"]):
    G.add_edge(caller, callee,
               weight=g["duration_ms"].mean(),
               count=len(g),
               fail_rate=g["is_error"].mean())
```

#### 故障传播路径 BFS

- 异常 span：`is_error == True`
- 向上：沿 `parent_id` 找父 span，直到 root
- 向下：找 `parent_id` 指向当前 span 的子 span
- 路径：`caller → callee` 的边序列

## 7. RCA100

### 7.1 RCA100 调用链（trace）

> 待补充。

### 7.2 待补清单

后续确认以下 RCA100 文件的字段后补充：

- `cases/t00x/metrics.parquet`
- `cases/t00x/logs.parquet`
- `cases/t00x/traces.parquet`
- `cases/t00x/events.parquet`
- `cases/t00x/alerts.parquet`
- `task.json`
- `topology.json`