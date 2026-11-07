# 字段映射表

## 目录

1. AIOps 2025 日志
2. AIOps 2025 调用链
3. AIOps 2025 指标：apm/service 与 apm/pod
4. AIOps 2025 指标：infra/*
5. AIOps 2025 故障标注：groundtruth.jsonl
6. GAIA（待补）
7. RCA100（待补）

---

## AIOps 2025 日志

| 原始字段 | 含义 | 标准化字段 | 示例 / 说明 |
|---|---|---|---|
| @timestamp | 日志时间（UTC） | timestamp | 2025-06-05T19:00:00.221Z |
| k8_namespace | 命名空间 | namespace | hipstershop |
| agent_name | Filebeat agent | agent | filebeat-filebeat-hdb2p |
| k8_pod | Pod 名 | pod | cartservice-0；StatefulSet 格式 |
| k8_node_name | 节点名 | node | aiops-k8s-07 |
| message | 日志正文 | message | 三种格式：ANSI/JSON/HTTP |
| （派生）k8_pod | 服务名 | service | 去掉末尾 `-数字` |
| （派生）message | 级别 | level | JSON severity → ANSI 清洗后正则 → HTTP 状态码 |
| （缺失）— | 调用链 ID | trace_id | 日志无；对齐时以 trace 为主表 |

## AIOps 2025 调用链

| 原始字段 | 含义 | 标准化字段 | 示例 / 说明 |
|---|---|---|---|
| traceID | 调用链 ID | trace_id | 32 位十六进制 |
| spanID | span ID | span_id | 16 位十六进制 |
| references[refType=CHILD_OF].spanID | 父 span | parent_id | root span 为空 |
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
| （派生）parent_id + service | 调用方 | caller | join 父 span 的 service |
| （派生）当前 service | 被调用方 | callee | 当前 span 的 service |
| logs | span 事件 | — | 可忽略 |
| flags | 采样标志 | — | 可忽略 |

### 关键说明
- 无现成 caller/callee，必须从 parent_id join 派生。
- duration 单位微秒，转毫秒除以 1000。
- 时区 UTC，与日志 @timestamp 一致。
- 同一条 trace 里多条 span 的 parent_id 相同，说明是并行子 span（如 frontend 调 productcatalog 两次）。

## AIOps 2025 指标：apm/service 与 apm/pod（宽表）

| 原始字段 | 含义 | 标准化字段 | 示例 / 说明 |
|---|---|---|---|
| time | 时间（UTC，分钟级） | timestamp | 2025-06-05T16:00:00Z，ISO 8601 字符串 |
| object_id | 实体 ID | entity | service 下是服务名；pod 下是 Pod 名 |
| object_type | 实体类型 | entity_type | service / pod |
| request | 请求量 | request | 计数 |
| response | 响应量 | response | 计数 |
| rrt | 平均响应时间 | rrt | 对应 groundtruth 的 rrt |
| rrt_max | 最大响应时间 | rrt_max | 对应 groundtruth 的 rrt_max |
| error | 错误数 | error | 计数 |
| error_ratio | 错误率 | error_ratio | 比例 |
| client_error | 客户端错误数 | client_error | 对应 groundtruth 的 client_error |
| client_error_ratio | 客户端错误率 | client_error_ratio | 对应 groundtruth |
| server_error | 服务端错误数 | server_error | 计数 |
| server_error_ratio | 服务端错误率 | server_error_ratio | 比例 |
| timeout | 超时数 | timeout | 计数 |

### 说明
- 每个文件是一个实体一整天的数据，一分钟一行。
- 读入后 `timestamp` 直接 `pd.to_datetime(df["time"], utc=True)` 即可。
- 不需要按维度聚合，因为一个实体一个时间点只有一行。

## AIOps 2025 指标：infra/*（窄表）

| 原始字段 | 含义 | 标准化字段 | 示例 / 说明 |
|---|---|---|---|
| time | 时间（UTC，分钟级） | timestamp | 2025-06-05T16:00:00Z |
| kpi_key | 英文指标名 | metric | pod_fs_reads_bytes；与值列名一致，可信 |
| kpi_name | 中文描述 | metric_cn | 累计读取字节数；仅展示，不参与逻辑 |
| <kpi_key 同名列> | 指标值 | value | 列名随文件变化，排除法找 |
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
- 值列名 = `kpi_key` = 英文指标名（如 pod_fs_reads_bytes）。
- `kpi_name` 是中文描述（如 累计读取字节数），仅展示。
- 同一 timestamp 有多行（device/mountpoint/type 维度不同），按 `timestamp + entity + metric` 聚合。
- 聚合后每实体每分钟 1 行。
- 与 groundtruth.key_metrics 匹配时，用 `kpi_key`，不用 `kpi_name`。

## AIOps 2025 故障标注（groundtruth.jsonl）

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


### GAIA 故障注入记录（run_table）

| 原始字段 | 含义 | 标准化字段 | 示例 / 说明 |
|---|---|---|---|
| datetime | 日期 | date | 2021-07-01；仅日期 |
| service | 服务名 | service | dbservice1 |
| message | 故障描述 | fault_message | 自然语言，含故障类型、开始时间、时长、资源量 |

### message 正则提取规则

| 提取项 | 正则 | 示例 |
|---|---|---|
| 故障类型 | `\[(.*?)\]` | memory_anomalies |
| 开始时间 | `start at (.+?) and` | 2021-07-01 11:44:26.882752 |
| 持续时长 | `lasts (\d+) seconds` | 600 |
| 资源量 | `use (\d+\w+) memory` | 1g |
| 严重级别 | `\| (WARNING\|ERROR\|INFO) \|` | WARNING |

### 与 AIOps 2025 的差异
- GAIA run 表是半结构化，需正则解析 message；
- 无 key_observations，证据需从日志/指标中自行提取；
- 时间只有日期级，精确时间在 message 内。

## GAIA 指标（metric）

| 原始字段 | 含义 | 标准化字段 | 示例 / 说明 |
|---|---|---|---|
| timestamp | Unix 毫秒时间戳 | timestamp | 1627747202000 → UTC 时间；采样间隔 30 秒 |
| value | 指标值 | value | 6488064 |
| 文件名 | 实体+指标名 | entity, metric | 用正则拆分，见下 |

### 文件名解析
- 格式：`<实体>_<IP>_<指标名>_<开始日期>_<结束日期>.csv`
- 正则：`^(.+?_\d+\.\d+\.\d+\.\d+)_(.+)_(\d{4}-\d{2}-\d{2})_(\d{4}-\d{2}-\d{2})\.csv$`
- 实体例：`dbservice1_0.0.0.4`
- 指标例：`docker_memory_stats_mapped_file`、`docker_cpu_core_7_norm_pct`
- 文件数：10817 个，一指标一文件

### 关键说明
- 时间单位毫秒，`pd.to_datetime(df["timestamp"], unit="ms", utc=True)`。
- 采样间隔 30 秒，与 AIOps 2025 的 60 秒不同，对齐时需重采样到统一频率。
- 指标名和实体只在文件名里，读文件时必须同时解析文件名。

### 指标规模与筛选策略

- metric 文件总数：10817
- CPU 类占 3024 个（24 核 × 42 实体 × 3 类），必须聚合
- 非 CPU 指标每实体一个文件，36-42 个实体

| 类别 | 原始指标 | 保留策略 | 聚合 |
|---|---|---|---|
| CPU | docker_cpu_core_N_pct / _ticks / _norm_pct | 只保留 _pct | 24 核取 mean |
| 内存 | docker_memory_usage_pct / total / fail_count | 保留 3 个 | 原样 |
| 磁盘 | docker_diskio_read_bytes / write_bytes / write_rate / write_queued | 保留 4 个 | 原样 |
| 网络 | docker_network_in/out_bytes / errors / dropped | 保留 6 个 | 原样 |
| 系统 | system_* | 待全量统计 | 原样 |
| 中间件 | redis_* / tidb_* | 待全量统计 | 原样 |

### 聚合规则
- CPU `_pct`：24 核取 mean，得到实体级 CPU 使用率
- 累加型 `_ticks`、`_bytes_total`：先 sum 再一阶差分，避免绝对值无意义
- 比率型 `_pct`、`_ratio`：直接 mean 或 max
- 聚合后每实体每分钟一行

### 指标前缀分布（去重后 ~591 个指标名）

| 前缀 | 指标名数 | 占比 | 处理 |
|---|---|---|---|
| system | 385 | 65% | 需按子类（cpu/memory/network/filesystem/disk）细分后筛选 |
| docker | 90 | 15% | CPU 已聚合，其余按内存/磁盘/网络保留 |
| redis | 89 | 15% | 保留连接数、命中率、内存、延迟类 |
| zookeeper | 27 | 5% | 保留连接数、延迟、节点数类 |

### 筛选原则
- system 类：按子类保留 5-10 个核心指标，去掉按核心/设备展开的重复维度
- docker 类：CPU 聚合成 1 个，内存 3 个，磁盘 4 个，网络 6 个
- redis/zookeeper：保留与故障强相关的 5-10 个
- 最终目标：50-80 个核心指标，对应开题中的特征筛选目标

### system 类指标筛选清单

system 类共 385 个指标名、约 5000+ 文件，按子类砍到 15-20 个：

| 子类 | 文件数 | 保留 | 砍掉 |
|---|---|---|---|
| network_summary | 2281 | tcp_established, tcp_retrans, socket_used | 其余 TCP/UDP 细分计数器 |
| process_cgroup | 507 | 全部砍掉（与 docker 类冗余） | 全部 |
| diskio_iostat | 143 | await, util | 其余 |
| memory_* | ~340 | used_pct, actual_free, swap_used, page_swap_in_rate | hugepages、page 细项 |
| cpu_* | ~200 | user_pct, system_pct, iowait_pct, idle_pct | 各 core 展开、norm_pct |
| process_summary | 93 | running, sleeping, zombie | dead、idle、unknown、stopped |
| fsstat_total | 52 | used_pct, free | size_total、files |
| load_norm | 33 | 1, 5, 15 | 其余 |
| 其他 | — | 按需保留 3-5 个 | — |

### 总体筛选目标
- docker 类：CPU 聚合 1 个 + 内存 3 + 磁盘 4 + 网络 6 ≈ 14 个
- system 类：15-20 个
- redis 类：5-8 个
- zookeeper 类：3-5 个
- 合计约 40-50 个核心指标，符合开题“保留 50-80 个”目标

## GAIA 调用链（trace）

| 原始字段 | 含义 | 标准化字段 | 示例 / 说明 |
|---|---|---|---|
| timestamp | 日志时间（秒级） | timestamp | 2021-07-01 09:57:04 |
| host_ip | 主机 IP | host_ip | 0.0.0.1 |
| service_name | 服务名 | service | webservice1 |
| trace_id | 调用链 ID | trace_id | 22741016938865b0 |
| span_id | span ID | span_id | 3a98d34fb3b6075e |
| parent_id | 父 span ID | parent_id | 0 表示 root；**直接给出，无需派生** |
| start_time | 开始时间（微秒） | start_time | 2021-07-01 09:57:04.255416 |
| end_time | 结束时间（微秒） | end_time | 2021-07-01 09:57:04.268775 |
| url | 请求 URL | url | http://0.0.0.1:9379/web_login_service |
| status_code | HTTP 状态码 | status | 500 / 200 / 300 |
| message | 调用描述 | message | request call function 1 ... |

### 派生字段
| 派生 | 方式 | 标准化字段 |
|---|---|---|
| 持续时间 | `end_time - start_time`，单位微秒 | duration_us |
| 调用方 | 按 trace_id + parent_id join 父 span 的 service | caller |
| 被调用方 | 当前 span 的 service | callee |

### 关键说明
- **parent_id 直接给出**，root 为 `0`，不需要从嵌套结构解析，比 AIOps 2025 简单。
- `status_code` 是 HTTP 状态码（200/300/500），不是 span 的 status.code，异常判定用 `status_code >= 400`。
- 时间精度：`start_time`/`end_time` 是微秒级字符串，`timestamp` 是秒级，duration 要自己算。
- 文件按服务+月份切分：`trace_table_<服务>_<YYYY-MM>.csv`，10 个服务 × 月份数。
- **没有 `caller` / `callee` 字段**，需按 parent_id join 派生，和 AIOps 2025 一样。

## GAIA（待补）

> 待确认 `MicroSS/metric`、`MicroSS/trace`、`MicroSS/run`、`Companion_Data/*.zip` 的实际格式后填写。

## RCA100（待补）

> 待确认 `cases/t00x/metrics.parquet`、`logs.parquet`、`events.parquet`、`alerts.parquet` 字段后填写。