# AGENTS.md —— 本仓库自动化协作约定

多智能体航空客服系统（LangGraph × ReAct 真实工具调用 × SQLite 真实数据）。
接手前先读 README.md 与 GETTING_STARTED.md；本文档只讲"怎么干活"。

## Token 纪律（最重要）

LLM 调用消耗真实 token，一切验证优先零 token 手段：

1. **默认只跑 `python -m pytest test_unit.py -q`**（秒级、0 token、69 用例，独立临时库）。
2. 涉及管理端/订单流改动 → `python test_admin.py`（纯 REST+DB，≈0 token）。
3. **禁止主动运行 `test_regression.py`**——它发起 20+ 轮真实 LLM 对话且默认被
   `--go` 门禁阻止；只有用户明确要求时才带 `--go` 运行。
4. **不要用"发真实对话消息"来验证改动**。验证手段优先级：
   单测 → REST 探测（curl/requests）→ 看日志（flask.log / lg_dev.log）→ 查 DB →
   浏览器 UI 操作（界面渲染、点按钮、看面板，但不触发 LLM 的部分）。
   确需一轮真实对话验证时，先向用户说明成本并征得同意，且一轮说完所有验证点。
5. LangSmith 轨迹验证同理：看日志里的上报记录即可，不要为产生轨迹专门烧对话。

## 服务与启动

- LangGraph 运行时（端口 2024）：`.venv/Scripts/langgraph.exe dev --no-browser --no-reload`
- Web 服务（端口 5000）：`.venv/Scripts/python.exe web_app.py`
- 均在项目根目录后台运行，日志重定向 flask.log / lg_dev.log；
  Windows 下 langgraph dev 对文件监听不稳，改图相关代码后手动重启。
- Flask 以 debug=True 启动带重载器，改 .py 自动重启；模板即时生效。

## 开发习惯

- 每个功能步骤一个 git 提交并推送（中文提交信息，说明"是什么+为什么"）。
- 迁移一律幂等（db.py 的 `_ensure_column` / `CREATE TABLE IF NOT EXISTS`）。
- 写路径不经过 LLM：写库操作由 REST 层执行，LLM 只通过伪工具产生确认卡片。
- 单测与实现同提交；纯逻辑放仓库层并保持零重依赖（LLM 栈延迟导入）。
