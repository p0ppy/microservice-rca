## AIOps 2025 日志

| 原始字段 | 含义 | 标准化字段 | 示例 / 说明 |
|---|---|---|---|
| @timestamp | 日志采集时间（UTC） | timestamp | 2025-06-05T19:00:00.221Z；Z 表示 UTC，文件名日期为北京时间，差 8 小时 |
| k8_namespace | K8s 命名空间 | namespace | hipstershop；可用于过滤业务命名空间 |
| agent_name | Filebeat agent 名 | agent | filebeat-filebeat-hdb2p；辅助字段，不入模型 |
| k8_pod | Pod 名称 | pod | cartservice-0；StatefulSet 格式 `<service>-<ordinal>` |
| k8_node_name | 节点名称 | node | aiops-k8s-07；直接对应标准化 node |
| message | 日志正文 | message | 含 ANSI 转义码、JSON、纯文本三种格式，需清洗 |
| （派生）k8_pod | 服务名 | service | 去掉末尾 `-数字`：cartservice-0 → cartservice |
| （派生）message | 日志级别 | level | 三层提取：JSON severity → ANSI 清洗后正则 → HTTP 状态码推断 |
| （缺失）— | 调用链 ID | trace_id | 日志无此字段；对齐时以 trace 为主表，按 window+service+node 关联 |

### 日志格式说明
- **格式 A（.NET 控制台）**：message 含 ANSI 转义码，如 `ESC[32minfoESC[39m`，需先清洗再匹配级别。
- **格式 B（HTTP 访问日志）**：message 为纯文本，如 `Request finished HTTP/2 POST ... 200 ...`，无级别字段，按状态码推断。
- **格式 C（JSON 结构化）**：message 是 JSON 字符串，级别在 `severity` 字段。
- **命名空间**：本日归档均为 `hipstershop`，与 AIOps 2025 的 HipsterShop 技术栈一致。
- **时区**：`@timestamp` 为 UTC，文件名日期为 UTC+8，对齐时统一用 UTC。