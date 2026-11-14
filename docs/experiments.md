
## 2026-11-08 GAIA 日志加载（最终）

### GAIA business 小样本
- 文件：business_table_webservice1_2021-07.csv
- 读取行数：50000
- levels：INFO 49458, ERROR 542
- trace_id 长度分布：16（清洗后）
- trace_id 非空比例：待填
- missing ts：0
- 窗口聚合：41 行

### GAIA run
- 文件：run_table_2021-07.csv, run_table_2021-08.csv
- rows：36574
- services：10
- fault_type 非空：1507
- 时间：优先用 message 内 start at，无则回退 datetime 日期
- 说明：normal memory freed label 等恢复标记无精确时间，回退到 00:00:00 属正常

### GAIA run（最终验证）
- rows：36574
- fault 记录：1507
- memory_anomalies：1367 条，时间精度 100%
- cpu_anomalies：81 条，时间精度 100%
- Errno 111：30 条，fallback 到 datetime（非故障注入，合理）
- normal memory freed label：29 条，fallback（状态标记，合理）
- 整体 00:00:00 比例 96%，为系统日志，正常
- 结论：时间处理完成，故障注入记录精度 100%

### GAIA business（最终验证）
- trace_id 长度已确认 16
- trace_id 非空比例：100%（清洗后污染记录置 None 或修复）
- levels：INFO 为主，ERROR 542
- 结论：通过

## 2026-11-10 traces 最终验证

### AIOps 2025 trace
- 小样本：480,000 spans / 25,918 traces / 8 services
- error spans：67（gRPC 13/14/1）
- cross-service edges：186,837
- span_kind：client 290,169 / server 189,831（已统一小写）
- parent_id 非空：94.78%
- 10am 单文件：378,987 spans / 19,816 traces / 8 services / error 440
- 确认：8 个服务不是抽样偏差；adservice/currencyservice/paymentservice 未产生 span
- 时区：文件名 10-00-00 是北京时间，内容 UTC 02:00-03:00
- 10am 的 440 error 对应 groundtruth 02:02:59-02:15:59 network corrupt，时间吻合

### GAIA trace
- spans：200,000 / traces：40,241 / services：10
- error spans：8,943（HTTP 500 8,871 + 400 72）
- cross-service edges：139,823
- graph nodes/edges：10 / 30
- status：200 183,084 / 500 8,871 / 300 7,973 / 400 72
- 跨服务调用 top：webservice1 → mobservice2、logservice2 → logservice1 等，拓扑合理

## 2026-11-10 infra_pod 实体异常确认

### 时间点数最少的实体
- example-ant-10-29150880-n27z6：2 个时间点（K8s 测试 Pod，约 1 分钟生命周期）
- example-ant-29152320-kszxj：1316 个时间点（测试 Pod）
- example-ant-10-29152320-7q99m：1437 个时间点（测试 Pod）
- checkoutservice-1：2873 / 3 ≈ 958 分钟（约 8 小时数据缺失）
- checkoutservice-2：2877 / 3 ≈ 959 分钟（约 8 小时数据缺失）

### 时间点数最多的实体
- emailservice-1/0、currencyservice-1、productcatalogservice-0、adservice-0：均 4317 / 3 ≈ 1439 分钟/天

### 结论
- example-ant-* 是 K8s 测试 Pod，非业务服务，后续 align 时按缺失值过滤
- checkoutservice 有约 8 小时数据缺失，是真实特征，不影响故障窗口对齐
- max 4317 换算 1439 分钟/天，聚合正确

## 2026-11-11 infra_node 修复验证

### 问题
- pod 列是字符串 'null' 不是 NaN，notna() 返回 True
- _pick_infra_entity 误选 pod，11 个节点合并成 1 个 'null' 实体

### 修复
- 新增 _clean_null，把 'null'/'None'/'nan' 统一成 pd.NA
- pod 全 NA 时跳过，选 kubernetes_node

### 修复后结果
- rows：253,264
- entities：11（aiops-k8s-01~08 + k8s-master1~3）
- metrics：16
- 每实体时间点数：23,024（16 metric × 1,439 分钟）
- missing ts：0
- window rows：50,688
- 结论：修复生效，11 个节点全部正确区分

## 2026-11-13 GAIA metric 全量合并完成

### 合并结果
- rows: 578,917,111（与 7 个子类总和一致）
- entities: 21
- metrics: 589
- docker_cpu_core_* 残留: 0
- row groups: 2,897
- schema: 7 个文件一致
- time range: 2021-07-01 10:00:10 → 2021-08-31 15:59:44
- missing ts: 0
- 输出: outputs/demo/metrics_gaia_full.parquet

### entities 分类
- 业务服务 8：dbservice1/2, logservice1/2, mobservice1/2, webservice1/2
- 中间件 4：redis, redis_database, redisservice1/2, zookeeper
- system 节点：system_0.0.0.1/2/3/4
- 双 IP 重复实体（待归一化）：system_0.0.0.1_0.0.0.1 等 4 个

### 待处理
- 589 个 metric 需要按 field_mapping 保留清单筛选到 50-80 个
- 双 IP 实体需要归一化成单 IP
- 这两个在周六 align.py 里做

### 结论
- 合并成功，无丢行、无重复、CPU 聚合彻底
- 数据规模符合预期，可进入 align 阶段

## 2026-11-14 align.py AIOps 对齐验证

### 改进策略
- traces 作 base，logs 和 metrics left join
- logs 缺失填 0，metrics 缺失保留 NaN
- source_count：trace 恒 1，logs>0 加 1，metrics 非全 NaN 加 1

### AIOps 对齐结果
- rows：288
- windows：36
- services：8
- metric cols：77
- source_count：
  - 1：36（traces + metrics 全 NaN）
  - 2：245（85%）
  - 3：7（故障窗口）

### 3 源样本验证（7/7 匹配 groundtruth）
| 窗口 | log_error | trace_error | 匹配故障 |
|---|---|---|---|
| 2025-06-06 02:00 | 13 | 26 | network corrupt |
| 2025-06-06 04:00 | 32 | 0 | pod kill |
| 2025-06-06 07:00 | 20 | 0 | pod kill |
| 2025-06-06 10:00 | 498 | 0 | pod failure |
| 2025-06-06 12:00 | 20 | 0 | network corrupt |
| 2025-06-06 13:00 | 28 | 15 | pod kill |
| 2025-06-06 15:00 | 2 | 4 | memory stress |

匹配率：100%

### 数据限制
- 3 源样本的 service 全部是 frontend
- 原因：日志只有 frontend 有大量异常；trace 的 frontend span 数最多
- 影响：不能用"service=frontend"训练"根因是 recommendationservice"
- 解决：build_triplets.py 用 groundtruth 的 service 作为 root_service 标签，输入用整窗所有服务聚合特征

### 结论
- 对齐 pipeline 正确
- 时间窗口与 groundtruth 故障 100% 吻合
- 后续微调数据集构建需按"整窗聚合输入 + groundtruth 根因标签"设计
