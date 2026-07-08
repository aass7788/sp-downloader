# Vercel API 部署指南 (tikwm 代理引擎)

## 架构说明

```
┌─────────────────┐    POST /api/parse     ┌───────────────────────┐
│   客户端 GUI     │ ────────────────────▶ │  Vercel Function       │
│  (tiktok_gui)   │                        │  (tikwm 代理)          │
│                 │                        │                       │
│                 │                        │  转发请求到 tikwm API  │
│                 │                        │  tikwm 维护签名生成    │
│                 │ ◀──────────────────── │                       │
│                 │  JSON: CDN 直链列表     └───────────────────────┘
│                 │
│                 │    GET CDN 直链         ┌───────────────────────┐
│                 │ ────────────────────▶ │  TikTok / 抖音 CDN     │
│                 │ ◀──────────────────── │  (视频文件)            │
│                 │  视频文件流             └───────────────────────┘
└─────────────────┘
```

**核心引擎**: 代理 tikwm.com API

tikwm 服务端维护了 TikTok 移动端 API 的签名生成（X-Gorgon/X-Argus），Vercel 作为代理转发请求，解决国内网络直连 tikwm 被 Cloudflare 拦截的问题。

**设计思路**: Vercel 只做"代理解析"（轻量，1-3秒），"下载"在客户端直接从 CDN 获取（重流量，不走 Vercel）。

## 为什么不用 TikTok 移动端 API 直连？

TikTok 已升级安全机制，移动端 API 现在需要 X-Gorgon/X-Argus 签名头，不带签名会返回 429 限流。生成签名需要逆向 TikTok App 的 .so 原生库，纯 Python 做不到。tikwm 服务端帮我们解决了这个问题。

## 部署步骤

### 方法一：GitHub 关联部署（推荐）

```bash
# 1. 将 vercel_api/ 目录推到 GitHub 仓库

# 2. 在 Vercel 控制台 (vercel.com) 导入 GitHub 仓库

# 3. 配置:
#    - Framework Preset: Other
#    - Root Directory: vercel_api
#    - Build Command: (留空)
#    - Output Directory: (留空)

# 4. 点击 Deploy
```

### 方法二：Vercel CLI 部署

```bash
# 1. 安装 Vercel CLI
npm i -g vercel

# 2. 进入 vercel_api 目录
cd D:\py\tkxiazai\vercel_api

# 3. 登录 Vercel
vercel login

# 4. 部署
vercel --prod
```

## 环境变量（可选）

在 Vercel 控制台 -> Settings -> Environment Variables 中设置：

| 变量名 | 说明 | 示例 |
|--------|------|------|
| `API_KEY` | API 访问密钥（防止滥用） | `my-secret-key-123` |
| `ALLOWED_ORIGIN` | CORS 允许的域名 | `*` 或 `https://yourdomain.com` |

## API 用法

### 解析视频

```bash
# TikTok
curl -X POST https://your-app.vercel.app/api/parse \
  -H "Content-Type: application/json" \
  -d '{"url": "https://www.tiktok.com/@user/video/123456"}'

# 抖音
curl -X POST https://your-app.vercel.app/api/parse \
  -H "Content-Type: application/json" \
  -d '{"url": "https://www.douyin.com/video/123456"}'
```

响应：
```json
{
    "success": true,
    "video_id": "123456",
    "title": "视频标题",
    "author": "作者名",
    "cdn_urls": ["https://...", "https://..."],
    "cover_url": "https://...",
    "duration": 30,
    "platform": "tiktok"
}
```

### 健康检查

```bash
curl https://your-app.vercel.app/api/health
```

```json
{
    "status": "ok",
    "engine": "tikwm_proxy",
    "tikwm": true,
    "timestamp": 1751629057
}
```

## 客户端配置

部署完成后，修改 `download/config/download_providers.yaml`：

```yaml
providers:
  - type: tikwm
    enabled: true
    name: "tikwm"
    use_cloudscraper: true

  - type: vercel_api
    enabled: true
    name: "Vercel API"
    api_base: "https://your-app.vercel.app"  # 替换为你的 URL
    api_key: ""
    timeout: 30
```

## 文件结构

```
vercel_api/
├── api/
│   ├── parse.py        # POST/GET /api/parse - tikwm 代理解析
│   └── health.py       # GET /api/health - 健康检查
├── requirements.txt    # Python 依赖 (httpx)
├── .gitignore
└── README.md           # 本文件
```

## 限制与注意事项

1. **tikwm 限流**: tikwm 可能有请求频率限制，建议间隔 1-2 秒
2. **Cloudflare**: tikwm 启用了 CF 防护，Vercel 代理可绕过客户端 CF 拦截
3. **冷启动**: Vercel 函数冷启动约 1 秒
4. **图文帖**: 图文/相册帖子不返回视频下载链接
