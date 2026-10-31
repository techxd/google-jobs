# 專案狀態 (STATUS.md)

## Vercel 正式環境
1. **排球團 (`danny`)**：https://danny-pied.vercel.app
2. **二手相機 (`family`)**：https://family-six-bay.vercel.app
3. **Google 職缺 (`google-jobs`)**：https://google-jobs-iota.vercel.app

## 核心環境資訊
- **主機**：Ubuntu Linux (N200)，根目錄 `~/agent-projects`
- **CLI**：Git (`2.47.3`), `gh` (`2.46.0`), `vercel` (`65.9.0`)
- **服務**：Telegram Bot (`bot.py`) 整合 Antigravity
- **排程**：每日 08:00 AM 自動爬取 Google Careers 職缺並部署 (`cron` + GitHub Actions)

## 專案清單與自動化
- `danny`：Next.js 排球請假與報名系統 (GitHub + Vercel)
- `family`：PTT DC_SALE 二手相機搜尋小工具 (GitHub + Vercel)
- `google-jobs`：Google 職缺爬蟲與 RWD 頁面 (GitHub + Vercel，每日 08:00 自動更新)

## GitHub + Vercel 同步規範 (SOP)
- **全新專案**：`git init && git add . && git commit -m "feat: initial" && gh repo create <名稱> --public --source=. --remote=origin --push && vercel --prod --yes`
- **日常發布**：`git add . && git commit -m "<說明>" && git push && vercel --prod --yes`
- **忽略規範**：`.gitignore` 排除 `node_modules`, `.vercel`, `.next`, `.env*`
