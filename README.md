# Vercel API 部署指南 (TikTok 移动端 API 引擎)

## 架构说明

```
┌─────────────────┐    POST /api/parse     ┌───────────────────────┐
│   客户端 GUI     │ ────────────────────▶ │  Vercel Function       │
│  (tiktok_gui)   │                        │  (TikTok 移动端 API)   │
│                 │                        │                       │
│                 │                        │  1. 提取 aweme_id      │
│                 │                        │  2. GET api22-normal-c │
│                 │                        │     -alisg.tiktokv.com │
│                 │                        │     /aweme/v1/feed/    │
│                 │                        │  3. 提取无水印 CDN 直链│
│                 │ ◀──────────────────── │                       │
│                 │  JSON: CDN 直链列表     └───────────────────────┘
│                 │
│                 │    GET CDN 直链         ┌───────────────────────┐
│                 │ ────────────────────▶ │  TikTok / 抖音 CDN     │
│                 │ ◀──────────────────── │  (视频文件)            │
│                 │  视频文件流             └───────────────────────┘
└─────────────────┘
```

**核心引擎**: TikTok 移动端 API (`api22-normal-c-alisg.tiktokv.com/aweme/v1/feed/`)

与 [Evil0ctal/Douyin_TikTok_Download_API](https://github.com/Evil0ctal/Douyin_TikTok_Download_API) (douyin.wtf) 相同的底层逻辑：
- 直接构造带设备参数的 GET 请求打到 TikTok 移动端 API
- 移动 API 不区分普通/带货视频，都能返回无水印下载地址
- 不需要 Cookie、不需要 X-Gorgon 签名
- Vercel 海外服务器直连 TikTok API，无网络障碍

**设计思路**: Vercel 只做"解析"（轻量，1-3秒），"下载"在客户端直接从 CDN 获取（重流量，不走 Vercel）。完美规避 Serverless 的超时和流量限制。

## 与 yt-dlp 方案对比

| 维度 | 移动端 API (当前) | yt-dlp (旧方案) |
|------|-------------------|-----------------|
| 带货视频 | ✅ 支持 | ❌ 不支持 |
| 抖音 URL | ✅ 支持 | ❌ 不支持 |
| 冷启动 | ~1 秒 (httpx ~200KB) | ~3-5 秒 (yt-dlp ~50MB) |
| 依赖大小 | httpx (轻量) | yt-dlp (重量级) |
| 解析速度 | 1-2 秒 | 2-5 秒 |
| 无水印直链 | ✅ 直接返回 | ⚠️ 需额外处理 |
| 维护成本 | 低 (API 稳定) | 中 (extractor 需更新) |

## 部署步骤

### 方法一：Vercel CLI 部署（推荐）

```bash
# 1. 安装 Vercel CLI
npm i -g vercel

# 2. 进入 vercel_api 目录
cd D:\py\tkxiazai\vercel_api

# 3. 登录 Vercel
vercel login

# 4. 部署（首次会问几个问题，全部回车用默认值）
vercel --prod

# 5. 部署完成后会输出 URL，如：
#    https://tiktok-api-xxxx.vercel.app
```

### 方法二：GitHub 关联部署

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

### 方法三：Vercel 控制台拖拽部署

1. 打开 [vercel.com/new](https://vercel.com/new)
2. 将 `vercel_api/` 文件夹拖入页面
3. 点击 Deploy

## 环境变量（可选）

在 Vercel 控制台 -> Settings -> Environment Variables 中设置：

| 变量名 | 说明 | 示例 |
|--------|------|------|
| `API_KEY` | API 访问密钥（防止滥用） | `my-secret-key-123` |
| `ALLOWED_ORIGIN` | CORS 允许的域名 | `*` 或 `https://yourdomain.com` |

设置 `API_KEY` 后，客户端请求需携带 `Authorization: Bearer <key>` 头。

## API 用法

### 解析视频

**POST 方式：**
```bash
curl -X POST https://your-app.vercel.app/api/parse \
  -H "Content-Type: application/json" \
  -d '{"url": "https://www.tiktok.com/@user/video/123456"}'
```

**GET 方式（浏览器测试用）：**
```
https://your-app.vercel.app/api/parse?url=https://www.tiktok.com/@user/video/123456
```

**支持抖音链接：**
```bash
curl -X POST https://your-app.vercel.app/api/parse \
  -H "Content-Type: application/json" \
  -d '{"url": "https://www.douyin.com/video/123456"}'
```

**支持带货视频：**
```bash
# 带货视频也能解析，这是移动端 API 的核心优势
curl -X POST https://your-app.vercel.app/api/parse \
  -H "Content-Type: application/json" \
  -d '{"url": "https://www.tiktok.com/@seller/video/带货视频ID"}'
```

响应：
```json
{
    "success": true,
    "video_id": "123456",
    "title": "视频标题",
    "author": "作者名",
    "description": "描述内容",
    "cdn_urls": [
        "https://v16-webapp.tiktok.com/...",
        "https://v19-webapp.tiktok.com/..."
    ],
    "cover_url": "https://...",
    "duration": 30,
    "platform": "tiktok",
    "stats": {
        "play_count": 10000,
        "like_count": 500,
        "comment_count": 50,
        "share_count": 20
    },
    "aweme_type": 0
}
```

### 健康检查

```bash
curl https://your-app.vercel.app/api/health
```

响应：
```json
{
    "status": "ok",
    "engine": "tiktok_mobile_api",
    "tiktok": true,
    "douyin": false,
    "endpoints": 2,
    "timestamp": 1751629057
}
```

## 客户端配置

部署完成后，修改 `download/config/download_providers.yaml`：

```yaml
providers:
  - type: vercel_api
    enabled: true
    name: "Vercel API"
    api_base: "https://your-app.vercel.app"  # 替换为你的 URL
    api_key: ""                               # 设置了 API_KEY 则填入
    timeout: 30
```

## 文件结构

```
vercel_api/
├── api/
│   ├── parse.py        # POST/GET /api/parse - 移动端 API 解析
│   └── health.py       # GET /api/health - 健康检查
├── requirements.txt    # Python 依赖 (httpx)
├── vercel.json         # Vercel 部署配置
└── README.md           # 本文件
```

## Vercel 免费额度

| 指标 | 免费额度 | 说明 |
|------|----------|------|
| 函数调用 | 100K/月 | 个人使用绰绰有余 |
| 函数执行时间 | 100GB-Hours/月 | 每次解析约 1-2 秒 |
| 函数超时 | 10 秒 (Hobby) / 30 秒 (配置) | 解析足够，下载在客户端 |
| 部署数 | 每日 100 次 | 随便改 |
| 冷启动 | ~1 秒 | httpx 轻量依赖 |

## 技术细节

### TikTok 移动端 API 工作原理

```
1. 客户端发送 TikTok URL
   ↓
2. Vercel 提取 aweme_id (从 URL 路径或查询参数)
   ↓
3. 构造 GET 请求:
   GET https://api22-normal-c-alisg.tiktokv.com/aweme/v1/feed/
   ?iid=7318518857994389254
   &device_id=7318517321748022790
   &channel=googleplay
   &app_name=musical_ly
   &version_code=300904
   &aweme_id=1234567890
   ↓
4. API 返回 JSON:
   {
     "aweme_list": [{
       "aweme_id": "1234567890",
       "video": {
         "bit_rate": [
           {"bit_rate": 1100000, "play_addr": {"url_list": ["https://cdn..."]}},
           {"bit_rate": 500000, "play_addr": {"url_list": ["https://cdn..."]}}
         ],
         "play_addr": {"url_list": ["https://cdn..."]},
         "cover": {"url_list": ["https://cdn.../cover.jpg"]}
       },
       "author": {"unique_id": "username"},
       "desc": "视频描述",
       "statistics": {"play_count": 10000, ...}
     }]
   }
   ↓
5. 提取最高 bit_rate 的 play_addr URL (无水印)
   如 URL 含 playwm 则替换为 play (去水印兜底)
   ↓
6. 返回 CDN 直链列表给客户端
   ↓
7. 客户端直接从 CDN 下载视频 (不走 Vercel)
```

### 多端点故障转移

TikTok API 配置了两个端点自动故障转移：
1. `api22-normal-c-alisg.tiktokv.com` (新加坡)
2. `api22-normal-c-useast1a.tiktokv.com` (美东)

第一个端点失败自动切换到第二个，每个端点最多重试 2 次。

### 设备参数

内置的设备参数模拟一台 Android 设备的 TikTok App：
- `iid`: 安装 ID
- `device_id`: 设备 ID
- `channel`: 渠道 (googleplay)
- `app_name`: 应用名 (musical_ly)
- `version_code`: 版本号
- `device_platform`: 平台 (android)
- `device_type`: 设备型号
- `os_version`: 系统版本

这些参数不需要用户配置，程序内置即可使用。移动端 API 不校验 X-Gorgon 签名，只需基本 HTTP 头 + 设备参数查询串。

## 限制与注意事项

1. **CDN 403**: 部分 TikTok CDN 链接需要带 `Referer: https://www.tiktok.com/` 头，客户端已处理
2. **限流**: 高频请求可能被 TikTok API 限流，建议间隔 1-2 秒
3. **冷启动**: Vercel 函数冷启动约 1 秒 (httpx 轻量依赖)，首次请求稍慢
4. **抖音支持**: 抖音 API (`aweme.snssdk.com`) 可能比 TikTok API 更不稳定，建议主要用 TikTok
5. **图文帖**: 图文/相册帖子不返回视频下载链接
6. **带货视频**: 移动端 API 支持带货视频解析，这是相比 yt-dlp 的核心优势

## 与其他方案对比

| 维度 | Vercel 移动端 API | VPS Docker | 本地 NativeTikTok |
|------|-------------------|------------|-------------------|
| 成本 | $0 | $5/月 | $0 |
| 运维 | 零 | 需维护 | 零 |
| 带货视频 | ✅ | ✅ | ✅ |
| 抖音支持 | ✅ | ✅ | ❌ |
| 网络要求 | 客户端需访问 CDN | VPS 需在海外 | 客户端需访问 TikTok API |
| 部署速度 | 2 分钟 | 30 分钟 | 无需部署 |
| 冷启动 | ~1 秒 | 无 | 无 |
| 适合场景 | 推荐：免费+海外解析 | 重度使用 | 本地直连 |

**推荐组合**: 本地 `NativeTikTokProvider`（主力）+ Vercel API（备选，海外解析更稳定）
