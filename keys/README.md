# 支付密钥目录

放支付宝/微信的密钥文件。**只有本 README 允许提交，密钥文件本身已被 .gitignore 排除**
（`.key` / `.pem` / `.crt` / `.p12` / `.txt` 全在忽略列表里）。

## 当前使用的文件

| 文件 | 内容 | 从哪来 |
|---|---|---|
| `app_private.txt` | **应用私钥** | 密钥工具生成，或沙箱控制台「查看」复制 |
| `alipay_public.txt` | **支付宝公钥** | 沙箱控制台上传应用公钥后自动生成 |
| `app_public.txt` | 应用公钥 | **本项目用不到**，仅留档（它是你自己生成的，不是支付宝给你的） |

最容易踩的坑：把 `app_public.txt`（应用公钥）当成了支付宝公钥。
**验签用的是 `alipay_public.txt`（支付宝公钥）**，两者不同，放错会一直验签失败。

## 关于文件格式

`alipay_provider.py` 会自动规范化，下面两种都能用：

1. 带 PEM 标记（推荐，标准格式）
```
-----BEGIN RSA PRIVATE KEY-----
MIIEogIBAAKCAQEA...
-----END RSA PRIVATE KEY-----
```
2. **单行裸 Base64**（当前这几个文件就是这种）

> 说明：密钥工具导出的常常是单行裸 Base64，而 pycryptodome 的 `RSA.importKey`
> 只认 PEM、直接喂会报 `RSA key format is not supported`。
> 代码里 `_read_pem()` 检测到没有 `BEGIN` 标记时会自动补头尾，所以不用手动改文件。

公钥对应的标记是 `-----BEGIN PUBLIC KEY-----` / `-----END PUBLIC KEY-----`。

## 获取步骤

1. 进入 <https://open.alipay.com/develop/sandbox/app>
2. 选中的应用 → 开发信息 → 接口加签方式
3. 「查看」可直接复制应用私钥与支付宝公钥（默认密钥）；自定义密钥则用密钥工具生成后上传应用公钥
4. 复制 APPID 填到 `.env` 的 `ALIPAY_APP_ID`

## 安全

- 这些文件绝不进版本库、不贴聊天窗口、不上传网盘；
- 沙箱密钥泄露虽无资金风险，但生产密钥泄露等于把账户交给别人；
- 换机器时重新生成密钥对，不要拷贝旧文件。
