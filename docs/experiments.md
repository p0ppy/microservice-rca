
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
