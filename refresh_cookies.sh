#!/bin/bash
# YouTube 쿠키 갱신 + GitHub Secret 자동 업데이트
# 사용법: sh refresh_cookies.sh

set -e

REPO="cultivo-hy/summary-images"
TEST_URL="https://www.youtube.com/watch?v=6gvnDSAcZww"
COOKIE_FILE="youtube_cookies.txt"
FILTERED_FILE="youtube_cookies_filtered.txt"

echo "🍪 YouTube 쿠키 추출 중..."
yt-dlp --cookies-from-browser chrome \
  --cookies "$COOKIE_FILE" \
  --skip-download "$TEST_URL"

if [ ! -f "$COOKIE_FILE" ]; then
  echo "❌ 쿠키 파일 생성 실패"
  exit 1
fi

echo "✂️  YouTube 관련 쿠키만 필터링 중..."
# 헤더 줄 + youtube/google 관련 쿠키만 추출
head -5 "$COOKIE_FILE" > "$FILTERED_FILE"
grep -E "\.(youtube|google|googlevideo|ggpht)\.com" "$COOKIE_FILE" >> "$FILTERED_FILE" || true

ORIGINAL_SIZE=$(wc -c < "$COOKIE_FILE")
FILTERED_SIZE=$(wc -c < "$FILTERED_FILE")
echo "  원본: ${ORIGINAL_SIZE} bytes → 필터링: ${FILTERED_SIZE} bytes"

echo "☁️  GitHub Secret 업데이트 중..."
gh secret set YOUTUBE_COOKIES --repo "$REPO" < "$FILTERED_FILE"

echo "🗑️  로컬 쿠키 파일 삭제..."
rm "$COOKIE_FILE" "$FILTERED_FILE"

echo "✅ 완료! 쿠키가 갱신됐습니다."
