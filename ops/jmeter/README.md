# 阶梯压测（验收 1.2）

两条等价路径，指标口径一致：**JMeter 官方路线** 与 **零依赖 Python 路线**。

## 被测端点（两者一致）

| 端点 | 说明 |
|---|---|
| `POST /api/login` | 尾号登录（DB 读 + JWT 签发） |
| `GET /api/health` | 无鉴权基线（框架开销） |
| `GET /api/my/orders` | JWT + 订单聚合查询 |
| `GET /api/flights/search` | JWT + 航班检索（北京→上海） |

## 路线 A：JMeter（官方）

前置：JMeter 5.6+（需 JDK 8+）、Flask 已启动（`:5000`）。

```powershell
# 仓库根目录
pwsh -File ops/jmeter/run_jmeter.ps1
# 等价命令：
jmeter -n -t ops/jmeter/flight_agent.jmx -l ops/jmeter/results/jmeter_run.csv -e -o ops/jmeter/results/report_xxx
```

- 阶梯：**10 → 50 → 100 → 200** 并发，四组线程组**顺序**执行（`serialize_threadgroups=true`），
  每组 ramp 10~30s、持续 60s；
- 公共采样流在 `flight_agent_flow.jmx`（各线程组 IncludeController 引用）；
- 每轮循环 = 登录 → health → 订单 → 搜索 4 个采样，登录响应里的 `token`
  由 JSON Extractor 提取（`$.token`）给后续两个采样做 `Authorization: Bearer`；
- 结果：`results/jmeter_run.csv`（逐样本）+ `report_xxx/index.html`（HTML 聚合报告）。

> 登录账号默认 `M1001`（尾号 `2835`），改账号/端口在 jmx 的
> `TestPlan.user_defined_variables` 里调整（`MEMBER_ID` / `PHONE_SUFFIX` / `PORT`）。

## 路线 B：零依赖 Python（本仓库实测用）

无 JMeter/JDK 环境时的平替，同一套端点、同一阶梯：

```bash
.venv/Scripts/python.exe ops/loadtest/step_load.py --base http://127.0.0.1:5000 --member M1001 --suffix 2835
```

- 输出 `results/step_load_<ts>.csv`（逐请求）+ `step_load_<ts>_summary.json`
  （分档 QPS / P50 / P95 / P99 / 错误率）；
- 默认每档 30s（可 `--step-seconds` 调整），先 5s 预热（不计入）；
- 已实测结果与结论见 `eval/BASELINE.md` 第三节对比表。

## 结果落档

跑完把汇总数字填进 `eval/BASELINE.md` 第三节（对比表"数据来源"列注明
CSV / summary.json 文件名），保持"有基线、有对比、有来源"三要素。
