# 🚀 项目启动指南

本文档是**从零到跑通**的操作手册。项目原理与功能介绍见 [README.md](README.md)。

## 一、环境要求

| 依赖 | 版本 | 说明 |
|---|---|---|
| Python | 3.10+ | 建议 3.11/3.12 |
| pip | 随 Python | 用于安装依赖 |
| LLM API Key | — | DeepSeek / SiliconFlow 等 OpenAI 兼容接口均可 |

> Windows / macOS / Linux 均可运行。下文命令以 Windows（Git Bash）为例，macOS/Linux 去掉路径差异即可。

## 二、安装步骤

```bash
# 1. 进入项目目录
cd Customer-Agent

# 2. （推荐）创建并激活虚拟环境
python -m venv .venv
source .venv/Scripts/activate        # Windows Git Bash
# .venv\Scripts\activate             # Windows CMD/PowerShell
# source .venv/bin/activate          # macOS / Linux

# 3. 安装依赖
pip install -r requirements.txt
```

## 三、配置 .env

```bash
cp .env.example .env
```

编辑 `.env`，**必须**填写 API Key：

```ini
OPENAI_API_KEY=sk-xxxxxxxx        # 你的 Key（DeepSeek 官网或 SiliconFlow 申请）
OPENAI_BASE_URL=https://api.deepseek.com/v1
OPENAI_MODEL=deepseek-chat
FLASK_SECRET_KEY=change-me-to-a-random-string
```

> `.env` 必须放在项目根目录——`config.py` 按当前工作目录加载它。

## 四、启动服务（两个进程，都要启动）

### ① LangGraph 智能体服务（端口 2024）

```bash
langgraph dev --no-browser --no-reload
```

- 首次启动会自动创建 SQLite 数据库并生成种子数据（26 机场/10 航司/300+ 航班/未来30天票价）；
- 看到 `Application startup complete` 即就绪。

> Windows 提示：langgraph dev 的文件监听在部分环境下会崩溃，**修改代码后请手动重启此进程**。

### 可选：接入 LangSmith 轨迹观测（答辩加分项）

1. 到 [smith.langchain.com](https://smith.langchain.com) 免费注册并创建 API Key（`lsv2_pt_...`）；
2. 在 `.env` 中取消注释并填写：

   ```
   LANGSMITH_TRACING=true
   LANGSMITH_API_KEY=lsv2_pt_xxxxxxxx
   LANGSMITH_PROJECT=airline-customer-agent
   ```

3. 重启两个服务，Flask 启动日志出现「🔭 LangSmith 轨迹观测已启用」即生效；
4. 平台内即可看到每轮对话的完整轨迹：敏感词守卫 → 意图分类 → Agent 的 ReAct 循环 → 每次工具调用的入参与返回。零代码接入（LangChain 原生读取 `LANGSMITH_*` 环境变量）。

> 也可以换成自建 Langfuse（`pip install langfuse` + 回调处理器），演示效果类似。
> 若遇到 OpenBLAS 相关报错，用以下方式启动：
> `OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 langgraph dev --no-browser --no-reload`

### ② Web 服务（端口 5000）

新开一个终端（同一虚拟环境）：

```bash
python -c "import web_app; web_app.app.run(host='0.0.0.0', port=5000, debug=False)"
```

- 同时会拉起**订单生命周期后台任务**（每 5 分钟扫描，起飞航班自动转「已使用」）；
- 运行日志输出在当前终端（或重定向到文件：`... > web_out.log 2>&1`）。

### ③ 验证启动成功

```bash
curl http://127.0.0.1:2024/ok          # 期望 {"ok":true}
curl http://127.0.0.1:5000/api/health  # 期望 {"status":"healthy",...}
```

两个都通过后，浏览器打开：

- **客户端**：<http://127.0.0.1:5000> —— 登录会员 `M1001`，手机尾号 `2835`
- **管理端**：<http://127.0.0.1:5000/admin> —— 登录管理员 `admin / admin123`

## 五、30 秒功能体验路线

1. 客户端登录 → 说「帮我查一下明天北京到上海的经济舱」（看到流式回答 + 航班搜索工具动画）；
2. 「帮我订明天 CA1061 经济舱 2 个人」→ 确认卡片 → 确认预订 → 去支付；
3. 「查一下我的订单」/ 打开右上角「我的数据」面板；
4. 「帮我把订单 Oxxxxx 改签到下周三」→ 改签卡片；
5. 「帮我规划一个 3 天 2 晚的成都行程」→ 行程规划师（真实航班+天气表格）；
6. 「航班延误害我误了转机，我要投诉」→ 投诉落库，管理端可见。

## 六、常用操作

```bash
# 重置演示数据（清空所有订单/投诉/会话痕迹，重新种子）
python -c "from services.db_seed import reset_database; reset_database()"

# 跑回归测试（两个服务需处于运行状态）
python test_regression.py    # 客户端 24 用例
python test_admin.py         # 管理端 37 用例

# 重置数据库结构（危险：会清库重来）
python -c "from services.db_seed import reset_database; reset_database()"
```

## 七、故障排查

| 现象 | 原因与处理 |
|---|---|
| 聊天回复报「无法创建助手/线程」 | LangGraph 服务（2024）没启动或已崩溃 → 重启 `langgraph dev`；之后 Web 端自动恢复 |
| 前端显示「本地兜底 local_fallback」 | 同上，此时走进程内降级图，功能受限但可对话 |
| LLM 回复报 Connection error | API Key 无效/欠费/网络问题 → 检查 `.env` 与账户余额 |
| `502/连接拒绝` 访问 5000 | Flask 没启动，或端口被占用（`netstat -ano \| findstr :5000`） |
| 改了代码但行为没变 | langgraph dev 需**手动重启**才加载新代码（见第四节提示）；Flask 非调试模式模板有缓存，改模板后也要重启 |
| `database is locked` | SQLite 多进程并发写冲突，重试即可；避免外部工具长时间持有写事务 |
| 管理员登录 401 | 账号 `admin/admin123` 由种子自动创建；若重置过库会重新生成 |

## 八、停止服务

两个终端分别 `Ctrl+C` 即可。后台生命周期线程随 Flask 进程退出；停机期间过期的航班订单，
下次启动 Flask 时首轮扫描会自动补转为「已使用」。
