"""
Vercel Serverless Function: /api/parse
=======================================
解析 TikTok / 抖音 视频 URL，返回 CDN 无水印直链和元数据。

核心引擎: 代理 tikwm.com API
  - tikwm 服务端维护了 TikTok 移动端 API 签名（X-Gorgon/X-Argus）
  - Vercel 作为代理转发，解决国内网络直连 tikwm 被 Cloudflare 拦截的问题
  - tikwm 支持 TikTok + 抖音，支持带货视频

架构:
    客户端 -> Vercel (代理) -> tikwm API -> 返回 CDN 直链
    客户端 (本地下载)      -> 直接从 CDN 下载

请求:
    POST /api/parse
    Content-Type: application/json
    Body: {"url": "https://www.tiktok.com/@user/video/123456"}

    可选 Header:
    Authorization: Bearer <API_KEY>  (设置环境变量 API_KEY 后启用)

响应:
    {
        "success": true,
        "video_id": "123456",
        "title": "视频标题",
        "author": "作者名",
        "description": "描述",
        "cdn_urls": ["https://...", "https://..."],
        "cover_url": "https://...",
        "duration": 30,
        "platform": "tiktok"
    }
"""
import json
import re
import os
from urllib.parse import urlparse

import httpx


# tikwm API 端点
TIKWM_API_URL = "https://www.tikwm.com/api/"

# 请求超时（秒）
REQUEST_TIMEOUT = 15


def is_douyin_url(url: str) -> bool:
    """检测是否为抖音 (中国大陆) URL"""
    return any(domain in url for domain in [
        "douyin.com", "iesdouyin.com", "v.douyin.com",
    ])


def is_tiktok_url(url: str) -> bool:
    """检测是否为 TikTok (国际版) URL"""
    return any(domain in url for domain in [
        "tiktok.com", "vm.tiktok.com", "vt.tiktok.com",
    ])


def extract_video_id(url: str) -> str:
    """从 URL 中提取视频 ID"""
    match = re.search(r'/video/(\d+)', url)
    if match:
        return match.group(1)
    return "unknown"


def fetch_tikwm(url: str) -> dict | None:
    """调用 tikwm API 解析视频 URL

    tikwm 返回格式:
    {
        "code": 0,
        "data": {
            "id": "...",
            "title": "...",
            "author": {"nickname": "...", "unique_id": "..."},
            "hdplay": "https://...",
            "play": "https://...",
            "wmplay": "https://...",
            "cover": "https://...",
            "duration": 30,
            ...
        }
    }
    """
    try:
        with httpx.Client(timeout=REQUEST_TIMEOUT) as client:
            resp = client.get(
                TIKWM_API_URL,
                params={"url": url},
                headers={"User-Agent": "Mozilla/5.0"},
            )
        data = resp.json()
    except Exception:
        return None

    if data.get("code") != 0:
        return None

    return data.get("data")


def build_response(d: dict, original_url: str) -> dict:
    """从 tikwm 返回数据构建统一 API 响应"""
    platform = "douyin" if is_douyin_url(original_url) else "tiktok"
    vid = extract_video_id(original_url)
    if vid == "unknown":
        vid = str(d.get("id", "unknown"))

    # 提取下载链接（按质量优先级）
    cdn_urls = []
    for key in ["hdplay", "play", "wmplay"]:
        u = d.get(key, "")
        if u and u not in cdn_urls:
            # tikwm 返回的链接可能是相对路径，补全
            if u.startswith("/"):
                u = "https://www.tikwm.com" + u
            cdn_urls.append(u)

    author = d.get("author", {})
    title = d.get("title", "").strip()
    if not title:
        title = f"{platform}_{vid}"

    return {
        "success": True,
        "video_id": vid,
        "title": title[:200],
        "author": author.get("unique_id", "") or author.get("nickname", ""),
        "description": d.get("title", ""),
        "cdn_urls": cdn_urls[:5],
        "cover_url": d.get("cover", ""),
        "duration": d.get("duration", 0),
        "platform": platform,
    }


# ======================================================================
# Vercel Function 入口
# ======================================================================

def handler(request):
    """Vercel Python serverless function 入口"""
    # CORS 预检
    if request.method == "OPTIONS":
        return {
            "statusCode": 200,
            "headers": _cors_headers(),
            "body": "",
        }

    if request.method not in ("POST", "GET"):
        return _error_response(405, "Method not allowed. Use POST or GET.")

    # API Key 鉴权（可选）
    api_key = os.environ.get("API_KEY", "")
    if api_key:
        auth = request.headers.get("authorization", "")
        if auth != f"Bearer {api_key}":
            return _error_response(401, "Unauthorized")

    # 解析请求参数
    if request.method == "POST":
        try:
            body = request.json if hasattr(request, "json") else json.loads(request.body)
        except Exception:
            try:
                body = json.loads(request.body.decode("utf-8"))
            except Exception:
                return _error_response(400, "Invalid JSON body")
        url = body.get("url", "").strip()
    else:
        url = (
            request.query_params.get("url", "").strip()
            if hasattr(request, "query_params")
            else ""
        )

    if not url:
        return _error_response(400, 'Missing "url" parameter')

    if not is_tiktok_url(url) and not is_douyin_url(url):
        return _error_response(400, "URL must be a TikTok or Douyin link")

    # 调用 tikwm API
    try:
        data = fetch_tikwm(url)
        if not data:
            return _error_response(
                404,
                "Video not found. It may be private, deleted, or rate-limited.",
            )

        result = build_response(data, url)
        if result.get("cdn_urls"):
            return _success_response(result)
        else:
            return _error_response(404, "Failed to extract download URL.")

    except Exception as e:
        return _error_response(500, f"Parse error: {str(e)}")


# ======================================================================
# 响应工具函数
# ======================================================================

def _success_response(data):
    return {
        "statusCode": 200,
        "headers": _cors_headers() | {"Content-Type": "application/json"},
        "body": json.dumps(data, ensure_ascii=False),
    }


def _error_response(code, message):
    return {
        "statusCode": code,
        "headers": _cors_headers() | {"Content-Type": "application/json"},
        "body": json.dumps(
            {"success": False, "error": message}, ensure_ascii=False
        ),
    }


def _cors_headers():
    allowed_origin = os.environ.get("ALLOWED_ORIGIN", "*")
    return {
        "Access-Control-Allow-Origin": allowed_origin,
        "Access-Control-Allow-Methods": "POST, GET, OPTIONS",
        "Access-Control-Allow-Headers": "Content-Type, Authorization",
    }
