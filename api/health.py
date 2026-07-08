"""
Vercel Serverless Function: /api/health
========================================
健康检查端点，供客户端 DownloadManager 探测可用性。
检测 TikTok 移动端 API 是否可达。

响应:
    GET /api/health
    {"status": "ok", "engine": "tiktok_mobile_api", "tiktok": true, "douyin": false}
"""
import json
import os
import time
from urllib.parse import urlencode

import httpx


# 尝试从 parse.py 导入配置，失败则使用本地副本 (Vercel 环境兼容)
try:
    from api.parse import (
        TIKTOK_API_ENDPOINTS,
        DOUYIN_API_ENDPOINT,
        FEED_PATH,
        TIKTOK_DEVICE_PARAMS,
        DOUYIN_DEVICE_PARAMS,
        TIKTOK_HEADERS,
        DOUYIN_HEADERS,
    )
except ImportError:
    TIKTOK_API_ENDPOINTS = [
        "https://api22-normal-c-alisg.tiktokv.com",
        "https://api22-normal-c-useast1a.tiktokv.com",
    ]
    DOUYIN_API_ENDPOINT = "https://aweme.snssdk.com"
    FEED_PATH = "/aweme/v1/feed/"
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
    TIKTOK_HEADERS = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Referer": "https://www.tiktok.com/",
        "Cookie": "CykaBlyat=XD",
    }
    DOUYIN_HEADERS = {
        "User-Agent": "Mozilla/5.0 (Linux; Android 9; SM-ASUS_Z01QD) AppleWebKit/537.36",
        "Referer": "https://www.douyin.com/",
        "Cookie": "CykaBlyat=XD",
    }


def _check_api(endpoint, device_params, headers, timeout=10):
    """通用 API 健康检查"""
    params = dict(device_params)
    params["aweme_id"] = "7339393672959757570"  # 已知存在的测试视频
    query = urlencode(params)
    url = f"{endpoint}{FEED_PATH}?{query}"

    try:
        with httpx.Client(timeout=timeout) as client:
            resp = client.get(url, headers=headers)
        if resp.status_code == 200:
            data = resp.json()
            return bool(data.get("aweme_list"))
    except Exception:
        pass
    return False


def _check_tiktok_api() -> bool:
    """检测 TikTok 移动端 API 是否可达 (多端点故障转移)"""
    for endpoint in TIKTOK_API_ENDPOINTS:
        if _check_api(endpoint, TIKTOK_DEVICE_PARAMS, TIKTOK_HEADERS):
            return True
    return False


def _check_douyin_api() -> bool:
    """检测抖音移动端 API 是否可达"""
    return _check_api(DOUYIN_API_ENDPOINT, DOUYIN_DEVICE_PARAMS, DOUYIN_HEADERS)


def handler(request):
    """健康检查"""
    tiktok_ok = _check_tiktok_api()
    douyin_ok = _check_douyin_api()

    overall = "ok" if (tiktok_ok or douyin_ok) else "degraded"

    return {
        'statusCode': 200,
        'headers': {
            'Content-Type': 'application/json',
            'Access-Control-Allow-Origin': '*',
        },
        'body': json.dumps({
            'status': overall,
            'engine': 'tiktok_mobile_api',
            'tiktok': tiktok_ok,
            'douyin': douyin_ok,
            'endpoints': len(TIKTOK_API_ENDPOINTS),
            'timestamp': int(time.time()),
        })
    }
