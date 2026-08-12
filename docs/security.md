# 安全说明

## 凭据

- API Key 用 Windows DPAPI 加密后存 `data/workbench.db` 的 settings / model_profiles 表
- `data/` 已 gitignore，绝不入库
- **origin 变化自动清 Key**：改 base_url 的域名/端口（origin）后，旧 Key 不沿用，必须重新输入
- 日志、诊断包、导出不含 Key/正文/prompt

## 网络边界（本地桌面版）

- 只绑定 `127.0.0.1`，不监听局域网
- 三层防护（纵深）：
  1. **Host 白名单**：非 `127.0.0.1:8766` / `localhost:8766` → 403（防 DNS rebinding）
  2. **Origin 白名单**：写请求带非本地 Origin → 403（防同机恶意网页 CSRF）
  3. **随机 session token**：HttpOnly cookie + X-Wensu-Token 头；带错 token → 403
- CORS 白名单明确列出，禁止 `*`

## 抓取（SSRF）

- 所有外部抓取必须走 `safe_fetch`：仅 http/https、DNS 解析后内网/保留 IP 拦截
  （127.x / 10.x / 172.16-31.x / 192.168.x / 169.254.x / ::1 / 保留段）、
  重定向逐跳重新校验、1MB 上限、脚本剥离

## 内容安全

- 所有渲染文本 escapeHtml；结果链接只允许 http/https（safeUrl）
- 粘贴白名单：paste 拦截为纯文本，杜绝 HTML/脚本注入
- 搜索降级结果带 source 字段（web=实时检索 / model=模型知识），前端诚实标注

## 越权

- Citation 创建校验：来源必须属于文章所在项目（跨项目引用拒绝）

## 已知边界（如实记录）

- session token 不防同机进程级攻击（计划承认不可防；桌面版假设单用户本机）
- 云部署（多用户认证/配额/租户隔离）明确**不支持**，不在本地版半实现
