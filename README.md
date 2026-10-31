# Google Careers 職缺總覽 (New Taipei City)

每日自動爬取 Google Careers 網站位於新北市（New Taipei City）的 Early & Mid 經驗等級職缺，並自動更新前端頁面與部署至 Vercel。

## 功能特點
- 僅使用 Python 標準函式庫，無第三方依賴。
- 支援響應式 RWD、關鍵字即時搜尋、經驗等級篩選與職缺收合展開。
- 自動化更新排程：
  - **GitHub Actions**：每天 08:00 AM (UTC+8) 自動執行爬取並推送到倉庫。
  - **Vercel**：串接 GitHub 與生產部署，推送後自動發布上線。
  - **本機 Crontab (N200)**：每日 08:00 定時執行 `update_and_deploy.sh` 雙重保險。

## 手動更新
```bash
./update_and_deploy.sh
```
