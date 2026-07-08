"""
Vercel Serverless Function: /api/health
========================================
健康检查端点，供客户端 DownloadManager 探测可用性。
检测 tikwm API 是否可达。

响应:
    GET /api/health
    {"status": "ok", "engine": "tikwm_proxy", "tikwm": true}
"""
import json
import os
import time

import httpx


TIKWM_API_URL = "https://www.tikwm.com/api/"


def _check_tikwm() -> bool:
    """检测 tikwm API 是否可达"""
    try:
        with httpx.Client(timeout=10) as client:
            resp = client.get(
                TIKWM_API_URL,
                params={"url": "https://www.tiktok.com/@tiktok/video/7339393672959757570"},
                headers={"User-Agent": "Mozilla/5.0"},
            )
        if resp.status_code == 200:
            data = resp.json()
            return data.get("code") == 0
    except Exception:
        pass
    return False


def handler(request):
    """健康检查"""
    tikwm_ok = _check_tikwm()
    overall = "ok" if tikwm_ok else "degraded"

    return {
        "statusCode": 200,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": "*",
        },
        "body": json.dumps({
            "status": overall,
            "engine": "tikwm_proxy",
            "tikwm": tikwm_ok,
            "timestamp": int(time.time()),
        }),
    }
