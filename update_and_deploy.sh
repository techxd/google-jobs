#!/usr/bin/env bash
set -e

export PATH="/usr/local/bin:/usr/bin:/bin:$PATH"
export HOME="/home/techxd"
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$DIR"

echo "[$(date '+%Y-%m-%d %H:%M:%S')] 🚀 執行 Google 職缺爬蟲..."
python3 google_jobs.py --output index.html

git add index.html google_job.html
if ! git diff --staged --quiet; then
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] 📦 檢測到職缺變動，提交並推送至 GitHub..."
  git commit -m "chore: auto-update jobs ($(date '+%Y-%m-%d %H:%M'))"
  git push origin main
else
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] ℹ️ 職缺內容無變動。"
fi

echo "[$(date '+%Y-%m-%d %H:%M:%S')] 🌐 部署至 Vercel 生產環境..."
vercel --prod --yes > /dev/null

echo "[$(date '+%Y-%m-%d %H:%M:%S')] ✅ 全部流程已順利完成！"
