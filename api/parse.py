"""
Vercel Serverless Function: /api/parse
=======================================
解析 TikTok / 抖音 视频 URL，返回 CDN 无水印直链和元数据。

核心引擎: TikTok 移动端 API (api22-normal-c-alisg.tiktokv.com/aweme/v1/feed/)
  - 与 douyin.wtf (Evil0ctal/Douyin_TikTok_Download_API) 相同的底层逻辑
  - 直接构造带设备参数的 GET 请求打到移动端 API
  - 移动 API 不区分普通/带货视频，都能返回无水印下载地址
  - 不需要 Cookie、不需要 X-Gorgon 签名
  - Vercel 海外服务器直连 TikTok API，无网络障碍

架构:
    Vercel (移动端 API 解析) -> 返回 CDN 直链
    客户端 (本地下载)        -> 直接从 CDN 下载

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
import time
from urllib.parse import urlencode, urlparse, parse_qs

import httpx


# ======================================================================
# TikTok 移动端 API 配置
# ======================================================================

# 多端点故障转移: alisg=新加坡, useast1a=美东
TIKTOK_API_ENDPOINTS = [
    "https://api22-normal-c-alisg.tiktokv.com",
    "https://api22-normal-c-useast1a.tiktokv.com",
]

# 抖音移动端 API (中国大陆)
DOUYIN_API_ENDPOINT = "https://aweme.snssdk.com"

FEED_PATH = "/aweme/v1/feed/"

# TikTok 设备参数 (来自 Evil0ctal 项目，模拟 Android TikTok App)
TIKTOK_DEVICE_PARAMS = {
    "iid": 7318518857994389254,
    "device_id": 7318517321748022790,
    "channel": "googleplay",
    "app_name": "musical_ly",
    "version_code": "300904",
    "device_platform": "android",
    "device_type": "SM-ASUS_Z01QD",
    "os_version": "9",
}

# 抖音设备参数
DOUYIN_DEVICE_PARAMS = {
    "iid": 7318518857994389254,
    "device_id": 7318517321748022790,
    "channel": "tencent",
    "app_name": "aweme",
    "version_code": "300904",
    "device_platform": "android",
    "device_type": "SM-ASUS_Z01QD",
    "os_version": "9",
}

# 请求头 (移动端 API 不校验签名，只需基本 HTTP 头)
TIKTOK_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/90.0.4430.212 Safari/537.36"
    ),
    "Referer": "https://www.tiktok.com/",
    "Cookie": "CykaBlyat=XD",
}

DOUYIN_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Linux; Android 9; SM-ASUS_Z01QD) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/90.0.4430.212 Mobile Safari/537.36"
    ),
    "Referer": "https://www.douyin.com/",
    "Cookie": "CykaBlyat=XD",
}

# 请求超时 (秒)
REQUEST_TIMEOUT = 15

# 最大重试次数
MAX_RETRIES = 2


# ======================================================================
# URL 处理工具
# ======================================================================

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


def extract_aweme_id(url: str) -> str | None:
    """从各种 URL 格式中提取 aweme_id

    支持的格式:
        - https://www.tiktok.com/@username/video/1234567890
        - https://www.douyin.com/video/1234567890
        - https://www.iesdouyin.com/share/video/1234567890
        - 纯数字: 1234567890
        - 查询参数: ?aweme_id=1234567890
    """
    url = url.strip()

    # 纯数字 = 直接是 aweme_id
    if url.isdigit():
        return url

    # /video/ 后跟数字 (TikTok 和抖音通用)
    match = re.search(r'/video/(\d+)', url)
    if match:
        return match.group(1)

    # /share/video/ 后跟数字 (iesdouyin)
    match = re.search(r'/share/video/(\d+)', url)
    if match:
        return match.group(1)

    # 查询参数 aweme_id=xxx
    parsed = urlparse(url)
    if parsed.query:
        params = parse_qs(parsed.query)
        if 'aweme_id' in params:
            return params['aweme_id'][0]

    return None


def resolve_short_url(url: str) -> str:
    """跟随短链重定向，获取最终 URL

    vm.tiktok.com / v.douyin.com 短链会 301/302 到完整 URL
    """
    try:
        headers = TIKTOK_HEADERS if is_tiktok_url(url) else DOUYIN_HEADERS
        with httpx.Client(follow_redirects=True, timeout=10) as client:
            resp = client.get(url, headers=headers)
            return str(resp.url)
    except Exception:
        return url


# ======================================================================
# TikTok 移动端 API 调用
# ======================================================================

def fetch_tiktok_aweme(aweme_id: str) -> dict | None:
    """调用 TikTok 移动端 API 获取单个视频数据

    GET https://api22-normal-c-alisg.tiktokv.com/aweme/v1/feed/?iid=...&aweme_id=...

    返回 aweme_list[0] 或 None
    """
    params = dict(TIKTOK_DEVICE_PARAMS)
    params["aweme_id"] = aweme_id
    query = urlencode(params)

    # 多端点故障转移
    for endpoint in TIKTOK_API_ENDPOINTS:
        url = f"{endpoint}{FEED_PATH}?{query}"

        for attempt in range(MAX_RETRIES):
            try:
                with httpx.Client(timeout=REQUEST_TIMEOUT) as client:
                    resp = client.get(url, headers=TIKTOK_HEADERS)

                if resp.status_code != 200:
                    break  # 端点不可用，换下一个

                try:
                    data = resp.json()
                except (json.JSONDecodeError, ValueError):
                    break  # 非 JSON 响应，可能被 WAF 拦截

                aweme_list = data.get("aweme_list")

                if not aweme_list:
                    status_code = data.get("status_code", 0)
                    if status_code != 0:
                        # 非零状态码 = 限流/异常，重试
                        time.sleep(1)
                        continue
                    # status_code=0 但空列表 = 视频确实不存在
                    return None

                aweme = aweme_list[0]

                # 校验 aweme_id 匹配
                if str(aweme.get("aweme_id", "")) != str(aweme_id):
                    return None

                return aweme

            except (httpx.HTTPError, httpx.TimeoutException, OSError):
                if attempt < MAX_RETRIES - 1:
                    time.sleep(1)

    return None


def fetch_douyin_aweme(aweme_id: str) -> dict | None:
    """调用抖音移动端 API 获取单个视频数据

    GET https://aweme.snssdk.com/aweme/v1/feed/?iid=...&aweme_id=...
    """
    params = dict(DOUYIN_DEVICE_PARAMS)
    params["aweme_id"] = aweme_id
    query = urlencode(params)

    url = f"{DOUYIN_API_ENDPOINT}{FEED_PATH}?{query}"

    for attempt in range(MAX_RETRIES):
        try:
            with httpx.Client(timeout=REQUEST_TIMEOUT) as client:
                resp = client.get(url, headers=DOUYIN_HEADERS)

            if resp.status_code != 200:
                return None

            try:
                data = resp.json()
            except (json.JSONDecodeError, ValueError):
                return None

            aweme_list = data.get("aweme_list")

            if not aweme_list:
                status_code = data.get("status_code", 0)
                if status_code != 0:
                    time.sleep(1)
                    continue
                return None

            aweme = aweme_list[0]

            if str(aweme.get("aweme_id", "")) != str(aweme_id):
                return None

            return aweme

        except (httpx.HTTPError, httpx.TimeoutException, OSError):
            if attempt < MAX_RETRIES - 1:
                time.sleep(1)

    return None


# ======================================================================
# CDN 直链提取
# ======================================================================

def extract_best_url(aweme: dict) -> tuple:
    """从 API 响应中提取最优无水印下载 URL

    提取策略 (按优先级):
        1. video.bit_rate 数组中最高 bit_rate 的 play_addr
        2. video.play_addr.url_list[0] (默认播放地址)
        3. URL 中 playwm -> play (去水印兜底)

    Returns:
        (no_watermark_url, watermark_url, cover_url)
    """
    video = aweme.get("video", {})

    # --- 1. 从 bit_rate 中找最高画质 ---
    best_url = None
    best_bitrate = -1

    for br_item in video.get("bit_rate", []):
        bitrate = br_item.get("bit_rate", 0)
        play_addr = br_item.get("play_addr", {})
        url_list = play_addr.get("url_list", [])
        if url_list and bitrate > best_bitrate:
            best_url = url_list[0]
            best_bitrate = bitrate

    # --- 2. 回退到 play_addr ---
    if not best_url:
        play_addr = video.get("play_addr", {})
        url_list = play_addr.get("url_list", [])
        if url_list:
            best_url = url_list[0]

    # --- 3. playwm -> play (去水印) ---
    if best_url and "playwm" in best_url:
        best_url = best_url.replace("playwm", "play")

    # 带水印 URL (作为最后备用)
    download_addr = video.get("download_addr", {})
    wm_urls = download_addr.get("url_list", [])
    wm_url = wm_urls[0] if wm_urls else None

    # 封面
    cover = video.get("cover", {})
    cover_urls = cover.get("url_list", [])
    cover_url = cover_urls[0] if cover_urls else None

    return best_url, wm_url, cover_url


def is_image_post(aweme: dict) -> bool:
    """检测是否为图文/相册帖子 (非视频)"""
    return bool(aweme.get("images")) and not aweme.get("video")


def build_response(aweme: dict, platform: str) -> dict | None:
    """从 aweme 数据构建 API 响应"""
    if is_image_post(aweme):
        return None

    best_url, wm_url, cover_url = extract_best_url(aweme)
    if not best_url:
        return None

    # 构建 CDN URL 列表 (无水印优先, 带水印兜底)
    cdn_urls = [best_url]
    if wm_url and wm_url != best_url:
        cdn_urls.append(wm_url)

    # 提取元数据
    aweme_id = str(aweme.get("aweme_id", ""))
    author = aweme.get("author", {})
    stats = aweme.get("statistics", {})
    video = aweme.get("video", {})

    # 标题: desc 优先, 空则用 aweme_id
    desc = aweme.get("desc", "").strip()
    title = desc[:200] if desc else f"{platform}_{aweme_id}"

    return {
        "success": True,
        "video_id": aweme_id,
        "title": title,
        "author": author.get("unique_id", "") or author.get("nickname", ""),
        "description": desc,
        "cdn_urls": cdn_urls[:5],
        "cover_url": cover_url or "",
        "duration": video.get("duration", 0),
        "platform": platform,
        "stats": {
            "play_count": stats.get("play_count", 0),
            "like_count": stats.get("digg_count", 0),
            "comment_count": stats.get("comment_count", 0),
            "share_count": stats.get("share_count", 0),
        },
        "aweme_type": aweme.get("aweme_type", 0),
    }


# ======================================================================
# Vercel Function 入口
# ======================================================================

def handler(request):
    """Vercel Python serverless function 入口

    Args:
        request: Vercel Request 对象

    Returns:
        Vercel Response (dict 格式)
    """
    # CORS 预检
    if request.method == 'OPTIONS':
        return {
            'statusCode': 200,
            'headers': _cors_headers(),
            'body': ''
        }

    if request.method not in ('POST', 'GET'):
        return _error_response(405, 'Method not allowed. Use POST or GET.')

    # API Key 鉴权（可选）
    api_key = os.environ.get('API_KEY', '')
    if api_key:
        auth = request.headers.get('authorization', '')
        if auth != f'Bearer {api_key}':
            return _error_response(401, 'Unauthorized')

    # 解析请求参数 (支持 POST body 和 GET query)
    if request.method == 'POST':
        try:
            body = request.json if hasattr(request, 'json') else json.loads(request.body)
        except Exception:
            try:
                body = json.loads(request.body.decode('utf-8'))
            except Exception:
                return _error_response(400, 'Invalid JSON body')
        url = body.get('url', '').strip()
    else:
        # GET 请求: /api/parse?url=...
        url = request.query_params.get('url', '').strip() if hasattr(request, 'query_params') else ''

    if not url:
        return _error_response(400, 'Missing "url" parameter')

    # 验证 URL
    if not is_tiktok_url(url) and not is_douyin_url(url):
        return _error_response(400, 'URL must be a TikTok or Douyin link')

    platform = "douyin" if is_douyin_url(url) else "tiktok"

    # 提取 aweme_id
    aweme_id = extract_aweme_id(url)

    if not aweme_id:
        # 短链，需要跟随重定向
        try:
            resolved = resolve_short_url(url)
            aweme_id = extract_aweme_id(resolved)
        except Exception:
            pass

    if not aweme_id:
        return _error_response(400, 'Could not extract video ID from URL')

    # 调用移动端 API
    try:
        if platform == "douyin":
            aweme = fetch_douyin_aweme(aweme_id)
        else:
            aweme = fetch_tiktok_aweme(aweme_id)

        if not aweme:
            return _error_response(
                404,
                'Video not found. It may be private, deleted, or rate-limited.'
            )

        result = build_response(aweme, platform)
        if result:
            return _success_response(result)
        else:
            return _error_response(
                404,
                'Failed to extract download URL. This may be an image post.'
            )

    except Exception as e:
        return _error_response(500, f'Parse error: {str(e)}')


# ======================================================================
# 响应工具函数
# ======================================================================

def _success_response(data):
    return {
        'statusCode': 200,
        'headers': _cors_headers() | {'Content-Type': 'application/json'},
        'body': json.dumps(data, ensure_ascii=False)
    }


def _error_response(code, message):
    return {
        'statusCode': code,
        'headers': _cors_headers() | {'Content-Type': 'application/json'},
        'body': json.dumps({'success': False, 'error': message}, ensure_ascii=False)
    }


def _cors_headers():
    """CORS 头部"""
    allowed_origin = os.environ.get('ALLOWED_ORIGIN', '*')
    return {
        'Access-Control-Allow-Origin': allowed_origin,
        'Access-Control-Allow-Methods': 'POST, GET, OPTIONS',
        'Access-Control-Allow-Headers': 'Content-Type, Authorization',
    }
