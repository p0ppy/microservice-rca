
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
