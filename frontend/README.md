# 智能航空客服系统 · Vue 3 前端

Vue 3 + Vite 工程化前端，包含会员端与管理端两个入口。

## 目录

```text
frontend/
├── index.html          # 会员端入口
├── admin.html          # 管理端入口
├── src/
│   ├── api.js          # 统一 JWT Bearer API 请求封装
│   ├── styles/         # 全局基础样式
│   ├── client/         # 会员端（登录/AI客服/机票/值机/登机牌/个人中心/订单）
│   └── admin/          # 管理端（工作台/退款/投诉/航班/订单/机场航司/会员）
└── dist/               # npm run build 产物，由 Flask 服务
```

## 常用命令

```bash
# 安装依赖
npm install

# 本地开发（代理 /api 与 /admin/api 到 Flask 5000）
npm run dev

# 构建生产产物
npm run build
```

## 说明

- 会员端使用哈希路由，页面：`/#/chat`、`/#/flights`、`/#/checkin`、`/#/boardpass`、`/#/orders`、`/#/profile`。
- 管理端入口为 `/admin`，页面：`/#/dashboard`、`/#/refunds`、`/#/complaints`、`/#/flights`、`/#/orders`、`/#/base`、`/#/members`。
- 后端 Flask 只服务 `dist` 构建产物；修改前端源码后需重新执行 `npm run build`。
