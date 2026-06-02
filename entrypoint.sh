#!/bin/sh
set -e

echo "TikTok Downloader GUI starting..."
echo "yt-dlp version: $(yt-dlp --version)"

exec node server.js
