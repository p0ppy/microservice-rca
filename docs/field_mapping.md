# 字段映射表

## 目录
1. [AIOps 2025 日志](#aiops-2025-日志)
2. [AIOps 2025 调用链](#aiops-2025-调用链)
3. [AIOps 2025 指标：apm/service](#aiops-2025-指标apmservice宽表)
4. [AIOps 2025 指标：apm/pod](#aiops-2025-指标apmpod宽表)
5. [AIOps 2025 指标：infra/*](#aiops-2025-指标infra窄表)
6. [AIOps 2025 故障标注](#aiops-2025-故障标注groundtruthjsonl)
7. [RCA100 调用链](#rca100-调用链)
8. [GAIA](#gaia待补)

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

## AIOps 2025 指标：apm/pod（宽表）

| 原始字段 | 含义 | 标准化字段 | 示例 / 说明 |
|---|---|---|---|
| time | 时间（UTC，分钟级） | timestamp | 2025-06-05T16:00:00Z |
| object_id | Pod 名 | entity | frontend-1 |
| object_type | 实体类型 | entity_type | pod |
| request | 请求量 | request | 计数 |
| response | 响应量 | response | 计数 |
| rrt | 平均响应时间 | rrt | 毫秒级 |
| rrt_max | 最大响应时间 | rrt_max | 毫秒级 |
| error | 错误数 | error | 计数 |
| error_ratio | 错误率 | error_ratio | 比例 |
| client_error | 客户端错误数 | client_error | 计数 |
| client_error_ratio | 客户端错误率 | client_error_ratio | 比例 |
| server_error | 服务端错误数 | server_error | 计数 |
| server_error_ratio | 服务端错误率 | server_error_ratio | 比例 |
| timeout | 超时数 | timeout | 计数 |

### 说明
- 字段与 apm/service 完全一致，唯一区别是 object_id 是 Pod 名而不是服务名。
- 一分钟一行，直接可用，不需要聚合。
- Pod 名格式 `<service>-<ordinal>`，可派生 service。
