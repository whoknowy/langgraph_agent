# 部署与运行手册（阶段四 · 验收 1.2 / 1.3）

> 覆盖：Docker Compose 一键起全栈、Prometheus /metrics、Grafana 看板、
> 阶梯压测。Langfuse 观测细节见 `docs/OBSERVABILITY.md`。

## 一键部署（docker compose up -d）

```bash
docker compose up -d        # 首次会构建 flight-agent 镜像（3~5 分钟）
docker compose ps           # 等全部 healthy（app 健康检查 20s 起效）
```

| 服务 | 地址 | 说明 |
|---|---|---|
| app | http://localhost:5000 | Flask（waitress 生产 WSGI），挂载 `./data`（SQLite）与 `./keys`（只读） |
| langgraph | http://127.0.0.1:2024/ok | 图服务（dev 服务器；生产建议换 `langgraph up`） |
| langfuse | http://localhost:3000 | LLM 轨迹观测（预设账号 admin@demo.local / demo123456） |
| prometheus | http://127.0.0.1:9090 | 抓取 `app:5000/metrics`（15s 间隔） |
| grafana | http://localhost:3001 | 看板已预置（admin / demo123456） |

容器内 app 通过服务名访问 LangGraph（`LANGGRAPH_API_URL=http://langgraph:2024`
已在 compose 覆盖）；`.env` 里的支付宝密钥路径随 `./keys` 挂载进容器。

### /metrics 暴露的指标

| 指标 | 类型 | 含义 |
|---|---|---|
| `http_requests_total{method,endpoint,status}` | Counter | 请求总量（endpoint 用 url_rule，无动态段） |
| `http_request_duration_seconds_bucket{endpoint}` | Histogram | 时延分布（Grafana 里算 P95/P99） |
| `langgraph_up` | Gauge | LangGraph 连通性（抓取时探测 `/ok`） |
| `audit_denied_total` / `audit_writes_total` | Gauge | 近 1 天越权拦截数 / 成功写操作数（抓取时查 `audit_logs`） |

未安装 `prometheus-client` 时 `/metrics` 返回提示文本、其余接口不受影响。

## 本机直跑（不用 Docker）

```bash
.venv/Scripts/python.exe web_app.py                              # Flask :5000
.venv/Scripts/langgraph.exe dev --no-browser --no-reload         # 图服务 :2024
```

`/metrics` 在本机方式下同样可用：http://127.0.0.1:5000/metrics

## 阶梯压测

见 `ops/jmeter/README.md`。两条等价路线：

```bash
# 零依赖 Python 路线（约 2 分钟，10→50→100→200）
.venv/Scripts/python.exe ops/loadtest/step_load.py --member M1001 --suffix 2835

# JMeter 官方路线
pwsh -File ops/jmeter/run_jmeter.ps1
```

首轮实测结果与结论（舒适区 ≤50 并发、orders 接口 240 笔订单聚合是瓶颈点）
已落档 `eval/BASELINE.md` 第三节。

## 常见问题

- **端口占用**：3000（Langfuse）、3001（Grafana）、5000（app）、2024（LangGraph）、
  9090（Prometheus）。冲突时改 compose 端口映射左侧。
- **app 容器健康检查失败**：多为首次建库慢；`docker compose logs app` 看日志，
  数据卷 `./data` 里 SQLite 首启自动建表+种子。
- **Grafana 无数据**：确认 Prometheus Targets 页（:9090/targets）里 `app:5000` 为 UP；
  容器间抓取走服务名，本机直跑 Flask 时需手动把 prometheus.yml 的目标改成
  `host.docker.internal:5000`。
