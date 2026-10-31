#!/usr/bin/env python3
"""Google Careers 職缺爬蟲與網頁伺服器工具。

自動抓取 Google Careers 網站上地點位於 New Taipei City 且經驗等級為
Early 與 Mid (Medium) 的所有職缺，並匯出成支援手機瀏覽、條列收合展開與互動篩選的
google_job.html 報告。支援單次執行或常駐 Web 伺服器（每日自動更新）。
僅使用 Python 標準函式庫實作，無須安裝第三方套件。
"""

import argparse
from dataclasses import dataclass
from datetime import datetime
import html
from html.parser import HTMLParser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import re
import threading
from typing import Any
import urllib.parse
import urllib.request


BASE_SEARCH_URL = "https://www.google.com/about/careers/applications/jobs/results"
JOB_DETAIL_BASE_URL = "https://www.google.com/about/careers/applications/jobs/results/"
DEFAULT_LOCATION = "New Taipei City, Taiwan"
TARGET_LEVELS = ("EARLY", "MID")
DEFAULT_OUTPUT = Path("index.html")
DEFAULT_INTERVAL_SECONDS = 86400  # 24 小時（每天更新一次）
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

LEVEL_MAP: dict[int, str] = {
    0: "Intern & Apprentice",
    1: "Early",
    2: "Mid (Medium)",
    3: "Advanced",
    4: "Director+",
}


@dataclass
class JobPosting:
    """代表單一 Google 職缺資料結構。"""

    job_id: str
    title: str
    experience: str
    locations: list[str]
    minimum_qualifications: list[str]
    preferred_qualifications: list[str]
    url: str


class _ListHTMLParser(HTMLParser):
    """將職缺 HTML 片段中的 <li> 項目解析為純文字清單。"""

    def __init__(self) -> None:
        super().__init__()
        self.items: list[str] = []
        self._in_li = False
        self._current_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "li":
            self._in_li = True
            self._current_parts = []

    def handle_endtag(self, tag: str) -> None:
        if tag == "li" and self._in_li:
            self._in_li = False
            text = " ".join("".join(self._current_parts).split()).strip()
            if text:
                self.items.append(text)

    def handle_data(self, data: str) -> None:
        if self._in_li:
            self._current_parts.append(data)


def parse_html_list(raw_html: str) -> list[str]:
    """從包含 <ul><li>...</li></ul> 的 HTML 字串提取乾淨的條列文字。"""
    if not raw_html:
        return []
    parser = _ListHTMLParser()
    parser.feed(raw_html)
    if parser.items:
        return parser.items
    cleaned = re.sub(r"<[^>]+>", " ", raw_html)
    cleaned = " ".join(html.unescape(cleaned).split()).strip()
    return [cleaned] if cleaned else []


def split_qualifications(qual_html: str, fallback_min_html: str = "") -> tuple[list[str], list[str]]:
    """從職缺資格 HTML 拆分出 Minimum qualifications 與 Preferred qualifications。"""
    min_section = ""
    pref_section = ""

    min_match = re.search(
        r"Minimum qualifications:?</h3>(.*?)(?:<h3>Preferred qualifications:?</h3>|$)",
        qual_html,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if min_match:
        min_section = min_match.group(1)

    pref_match = re.search(
        r"Preferred qualifications:?</h3>(.*?)$",
        qual_html,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if pref_match:
        pref_section = pref_match.group(1)

    min_items = parse_html_list(min_section or fallback_min_html)
    pref_items = parse_html_list(pref_section)
    return min_items, pref_items


def build_search_url(location: str, target_levels: tuple[str, ...], page: int) -> str:
    """建構 Google Careers 搜尋結果頁面的 URL。"""
    params: list[tuple[str, str]] = [("location", location)]
    for level in target_levels:
        params.append(("target_level", level))
    if page > 1:
        params.append(("page", str(page)))
    query = urllib.parse.urlencode(params)
    return f"{BASE_SEARCH_URL}?{query}"


def extract_ds1_payload(page_html: str) -> list[Any]:
    """從 Google Careers 頁面 HTML 中提取 AF_initDataCallback (ds:1) JSON 資料。"""
    pattern = r"AF_initDataCallback\(\{key:\s*'ds:1',\s*hash:\s*'\d+',\s*data:"
    match = re.search(pattern, page_html)
    if not match:
        raise ValueError("無法在頁面中找到 ds:1 職缺資料區塊。")
    decoder = json.JSONDecoder()
    data, _ = decoder.raw_decode(page_html[match.end():])
    return data


def slugify_title(title: str) -> str:
    """將職位名稱轉為 URL 友善的 slug 字串。"""
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    return slug or "job"


def parse_job_entry(entry: list[Any], fallback_level: str | None = None) -> JobPosting | None:
    """解析單筆原始 JSON 陣列為 JobPosting 物件。"""
    if not entry or len(entry) < 20:
        return None

    job_id = str(entry[0]).strip()
    title = str(entry[1]).strip()

    raw_locations = entry[9] if isinstance(entry[9], list) else []
    locations: list[str] = []
    for loc in raw_locations:
        if isinstance(loc, list) and loc and isinstance(loc[0], str):
            locations.append(loc[0])

    if not any("new taipei" in loc.lower() for loc in locations):
        return None

    raw_level = entry[20] if len(entry) > 20 and isinstance(entry[20], int) else None
    if raw_level in LEVEL_MAP:
        experience = LEVEL_MAP[raw_level]
    elif fallback_level:
        experience = fallback_level
    else:
        experience = "Early / Mid"

    qual_html = ""
    if isinstance(entry[4], list) and len(entry[4]) > 1 and isinstance(entry[4][1], str):
        qual_html = entry[4][1]

    fallback_min_html = ""
    if isinstance(entry[19], list) and len(entry[19]) > 1 and isinstance(entry[19][1], str):
        fallback_min_html = entry[19][1]

    min_quals, pref_quals = split_qualifications(qual_html, fallback_min_html)
    url = f"{JOB_DETAIL_BASE_URL}{job_id}-{slugify_title(title)}"

    return JobPosting(
        job_id=job_id,
        title=title,
        experience=experience,
        locations=locations,
        minimum_qualifications=min_quals,
        preferred_qualifications=pref_quals,
        url=url,
    )


def fetch_google_jobs(
    location: str = DEFAULT_LOCATION,
    target_levels: tuple[str, ...] = TARGET_LEVELS,
) -> list[JobPosting]:
    """分頁抓取所有符合條件的 Google 職缺並去除重複項目。"""
    jobs_by_id: dict[str, JobPosting] = {}
    page = 1

    while True:
        url = build_search_url(location=location, target_levels=target_levels, page=page)
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=20) as resp:
            page_html = resp.read().decode("utf-8", errors="ignore")

        payload = extract_ds1_payload(page_html)
        raw_jobs = payload[0] if payload and isinstance(payload[0], list) else []
        total_count = payload[2] if len(payload) > 2 and isinstance(payload[2], int) else 0
        page_size = payload[3] if len(payload) > 3 and isinstance(payload[3], int) else 20

        if not raw_jobs:
            break

        for raw_entry in raw_jobs:
            job = parse_job_entry(raw_entry)
            if job and job.job_id not in jobs_by_id:
                jobs_by_id[job.job_id] = job

        if page * page_size >= total_count or len(raw_jobs) < page_size:
            break
        page += 1

    level_order = {"Early": 0, "Mid (Medium)": 1}
    return sorted(
        jobs_by_id.values(),
        key=lambda j: (level_order.get(j.experience, 99), j.title.lower()),
    )


def render_html(jobs: list[JobPosting], generated_at: str) -> str:
    """將職缺清單渲染為支援手機 RWD、條列收合展開與即時搜尋篩選的 HTML 頁面。"""
    early_count = sum(1 for j in jobs if j.experience == "Early")
    mid_count = sum(1 for j in jobs if j.experience == "Mid (Medium)")

    cards_html: list[str] = []
    for idx, job in enumerate(jobs, start=1):
        badge_class = "badge-early" if job.experience == "Early" else "badge-mid"
        min_li = "\n".join(
            f"<li>{html.escape(item)}</li>" for item in job.minimum_qualifications
        ) or "<li>未提供</li>"
        pref_li = "\n".join(
            f"<li>{html.escape(item)}</li>" for item in job.preferred_qualifications
        ) or "<li>未提供</li>"
        loc_text = " | ".join(html.escape(loc) for loc in job.locations)

        cards_html.append(
            f"""
            <details class="job-card" data-level="{html.escape(job.experience)}">
                <summary class="job-header">
                    <div class="job-title-group">
                        <span class="toggle-icon" aria-hidden="true">▸</span>
                        <span class="job-index">#{idx:02d}</span>
                        <h2 class="job-title">
                            <a href="{html.escape(job.url)}" target="_blank" rel="noopener noreferrer" onclick="event.stopPropagation();">
                                {html.escape(job.title)}
                            </a>
                        </h2>
                    </div>
                    <div class="job-header-right">
                        <span class="badge {badge_class}">{html.escape(job.experience)}</span>
                        <span class="expand-hint">詳細資訊</span>
                    </div>
                </summary>
                <div class="job-body">
                    <div class="job-meta">
                        <span>📍 {loc_text}</span>
                        <span>🆔 {html.escape(job.job_id)}</span>
                        <a class="apply-link" href="{html.escape(job.url)}" target="_blank" rel="noopener noreferrer">🔗 開啟職缺頁面 ↗</a>
                    </div>
                    <div class="qual-grid">
                        <section class="qual-box min-qual">
                            <h3>Minimum Qualifications</h3>
                            <ul>
                                {min_li}
                            </ul>
                        </section>
                        <section class="qual-box pref-qual">
                            <h3>Preferred Qualifications</h3>
                            <ul>
                                {pref_li}
                            </ul>
                        </section>
                    </div>
                </div>
            </details>
            """
        )

    cards_joined = "\n".join(cards_html)
    return f"""<!DOCTYPE html>
<html lang="zh-Hant">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=5.0">
    <meta name="theme-color" content="#1a73e8">
    <title>Google Careers 職缺清單 - New Taipei City (Early &amp; Mid)</title>
    <style>
        :root {{
            --g-blue: #1a73e8;
            --g-green: #1e8e3e;
            --g-yellow: #f9ab00;
            --bg: #f8f9fa;
            --card-bg: #ffffff;
            --text-main: #202124;
            --text-muted: #5f6368;
            --border: #dadce0;
        }}
        * {{
            box-sizing: border-box;
            -webkit-tap-highlight-color: transparent;
        }}
        body {{
            margin: 0;
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Noto Sans TC", sans-serif;
            background-color: var(--bg);
            color: var(--text-main);
            line-height: 1.6;
        }}
        header {{
            background: linear-gradient(135deg, #1a73e8 0%, #174ea6 100%);
            color: #fff;
            padding: 1.5rem 1rem;
            text-align: center;
        }}
        header h1 {{
            margin: 0 0 0.35rem 0;
            font-size: clamp(1.25rem, 4vw, 1.75rem);
        }}
        header p {{
            margin: 0;
            opacity: 0.92;
            font-size: 0.86rem;
        }}
        .container {{
            max-width: 1140px;
            margin: 0 auto;
            padding: 1rem;
        }}
        .toolbar {{
            position: sticky;
            top: 0.5rem;
            z-index: 10;
            display: flex;
            flex-wrap: wrap;
            gap: 0.65rem;
            justify-content: space-between;
            align-items: center;
            background: rgba(255, 255, 255, 0.96);
            backdrop-filter: blur(8px);
            padding: 0.8rem 1rem;
            border-radius: 12px;
            border: 1px solid var(--border);
            margin-bottom: 1rem;
            box-shadow: 0 2px 8px rgba(0, 0, 0, 0.06);
        }}
        .filter-buttons {{
            display: flex;
            gap: 0.4rem;
            flex-wrap: wrap;
            width: 100%;
        }}
        @media (min-width: 768px) {{
            .filter-buttons {{
                width: auto;
            }}
        }}
        .filter-btn, .action-btn {{
            flex: 1;
            text-align: center;
            border: 1px solid var(--border);
            background: #fff;
            color: var(--text-main);
            padding: 0.45rem 0.8rem;
            border-radius: 999px;
            font-size: 0.86rem;
            cursor: pointer;
            font-weight: 500;
            white-space: nowrap;
            transition: all 0.15s ease;
        }}
        .filter-btn.active, .filter-btn:hover {{
            background: var(--g-blue);
            color: #fff;
            border-color: var(--g-blue);
        }}
        .action-btn {{
            background: #f1f3f4;
            border-color: transparent;
            color: var(--text-muted);
        }}
        .action-btn:hover {{
            background: #e8eaed;
            color: var(--text-main);
        }}
        .search-input {{
            width: 100%;
            padding: 0.5rem 0.85rem;
            border: 1px solid var(--border);
            border-radius: 8px;
            font-size: 0.92rem;
        }}
        @media (min-width: 768px) {{
            .search-input {{
                width: 300px;
            }}
        }}
        .job-list {{
            display: flex;
            flex-direction: column;
            gap: 0.5rem;
        }}
        .job-card {{
            background: var(--card-bg);
            border: 1px solid var(--border);
            border-radius: 10px;
            overflow: hidden;
            transition: box-shadow 0.15s ease, border-color 0.15s ease;
        }}
        .job-card:hover {{
            border-color: #bdc1c6;
        }}
        .job-card[open] {{
            box-shadow: 0 2px 8px rgba(0, 0, 0, 0.07);
            border-color: #aecbfa;
        }}
        .job-header {{
            list-style: none;
            display: flex;
            justify-content: space-between;
            align-items: center;
            gap: 0.75rem;
            padding: 0.85rem 1rem;
            cursor: pointer;
            user-select: none;
        }}
        .job-header::-webkit-details-marker {{
            display: none;
        }}
        .job-header:hover {{
            background-color: #f8fbff;
        }}
        .job-title-group {{
            display: flex;
            align-items: baseline;
            gap: 0.5rem;
            min-width: 0;
            flex: 1;
        }}
        .toggle-icon {{
            display: inline-block;
            color: var(--text-muted);
            font-size: 0.95rem;
            transition: transform 0.18s ease;
            flex-shrink: 0;
            width: 1rem;
            text-align: center;
        }}
        .job-card[open] .toggle-icon {{
            transform: rotate(90deg);
            color: var(--g-blue);
        }}
        .job-index {{
            color: var(--text-muted);
            font-weight: 600;
            font-size: 0.86rem;
            flex-shrink: 0;
        }}
        .job-title {{
            margin: 0;
            font-size: clamp(0.96rem, 2.8vw, 1.08rem);
            line-height: 1.4;
            font-weight: 600;
            word-break: break-word;
        }}
        .job-title a {{
            color: var(--g-blue);
            text-decoration: none;
        }}
        .job-title a:hover {{
            text-decoration: underline;
        }}
        .job-header-right {{
            display: flex;
            align-items: center;
            gap: 0.5rem;
            flex-shrink: 0;
        }}
        .expand-hint {{
            font-size: 0.78rem;
            color: var(--text-muted);
            display: none;
        }}
        @media (min-width: 640px) {{
            .expand-hint {{
                display: inline;
            }}
        }}
        .job-card[open] .expand-hint {{
            color: var(--g-blue);
        }}
        .badge {{
            display: inline-block;
            padding: 0.2rem 0.6rem;
            border-radius: 999px;
            font-size: 0.75rem;
            font-weight: 600;
            white-space: nowrap;
        }}
        .badge-early {{
            background: #e6f4ea;
            color: var(--g-green);
        }}
        .badge-mid {{
            background: #e8f0fe;
            color: var(--g-blue);
        }}
        .job-body {{
            padding: 0 1rem 1.1rem 1rem;
            border-top: 1px solid #f1f3f4;
            background: #fff;
        }}
        @media (min-width: 720px) {{
            .job-body {{
                padding: 0 1.35rem 1.35rem 1.35rem;
            }}
        }}
        .job-meta {{
            display: flex;
            flex-wrap: wrap;
            align-items: center;
            gap: 0.5rem 1rem;
            color: var(--text-muted);
            font-size: 0.84rem;
            padding: 0.75rem 0;
            margin-bottom: 0.75rem;
            border-bottom: 1px dashed #e8eaed;
        }}
        .apply-link {{
            margin-left: auto;
            color: var(--g-blue);
            text-decoration: none;
            font-weight: 500;
        }}
        .apply-link:hover {{
            text-decoration: underline;
        }}
        .qual-grid {{
            display: grid;
            grid-template-columns: 1fr;
            gap: 0.85rem;
        }}
        @media (min-width: 768px) {{
            .qual-grid {{
                grid-template-columns: 1fr 1fr;
                gap: 1.1rem;
            }}
        }}
        .qual-box {{
            background: #f8f9fa;
            border-radius: 8px;
            padding: 0.85rem 1rem;
            border-left: 4px solid var(--border);
        }}
        .qual-box.min-qual {{
            border-left-color: var(--g-blue);
        }}
        .qual-box.pref-qual {{
            border-left-color: var(--g-yellow);
        }}
        .qual-box h3 {{
            margin: 0 0 0.45rem 0;
            font-size: 0.92rem;
            color: var(--text-main);
        }}
        .qual-box ul {{
            margin: 0;
            padding-left: 1.2rem;
        }}
        .qual-box li {{
            margin-bottom: 0.32rem;
            font-size: 0.88rem;
            word-break: break-word;
        }}
        footer {{
            text-align: center;
            color: var(--text-muted);
            font-size: 0.82rem;
            padding: 1.5rem 1rem 2.5rem;
        }}
    </style>
</head>
<body>
    <header>
        <h1>Google Careers 職缺總覽 — New Taipei City</h1>
        <p>Location: New Taipei City ｜ Experience: Early &amp; Mid ｜ 更新時間：{html.escape(generated_at)}</p>
    </header>
    <main class="container">
        <div class="toolbar">
            <div class="filter-buttons">
                <button class="filter-btn active" onclick="filterJobs('ALL', this)">全部 ({len(jobs)})</button>
                <button class="filter-btn" onclick="filterJobs('Early', this)">Early ({early_count})</button>
                <button class="filter-btn" onclick="filterJobs('Mid (Medium)', this)">Mid ({mid_count})</button>
                <button class="action-btn" id="toggleAllBtn" onclick="toggleAll()">展開全部</button>
            </div>
            <input type="search" id="searchInput" class="search-input" placeholder="🔍 搜尋職位名稱或資格關鍵字..." oninput="applyFilters()">
        </div>
        <div id="jobList" class="job-list">
            {cards_joined}
        </div>
    </main>
    <footer>
        共收錄 {len(jobs)} 筆 Google Careers 職缺資料（點擊橫列可展開／收合資格條件，點擊職位名稱可直接前往官網）
    </footer>
    <script>
        let currentLevel = 'ALL';
        let allExpanded = false;

        function filterJobs(level, btn) {{
            currentLevel = level;
            document.querySelectorAll('.filter-btn').forEach(b => b.classList.remove('active'));
            btn.classList.add('active');
            applyFilters();
        }}

        function applyFilters() {{
            const query = document.getElementById('searchInput').value.toLowerCase();
            document.querySelectorAll('.job-card').forEach(card => {{
                const levelMatch = (currentLevel === 'ALL' || card.dataset.level === currentLevel);
                const textMatch = card.textContent.toLowerCase().includes(query);
                card.style.display = (levelMatch && textMatch) ? '' : 'none';
            }});
        }}

        function toggleAll() {{
            allExpanded = !allExpanded;
            document.querySelectorAll('.job-card').forEach(card => {{
                if (card.style.display !== 'none') {{
                    card.open = allExpanded;
                }}
            }});
            document.getElementById('toggleAllBtn').textContent = allExpanded ? '收合全部' : '展開全部';
        }}
    </script>
</body>
</html>
"""


def update_jobs_html(output_path: Path = DEFAULT_OUTPUT) -> tuple[int, int, int]:
    """抓取最新職缺並寫入 HTML 檔案，回傳 (總數, Early 數, Mid 數)。"""
    print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] 🔍 正在從 Google Careers 抓取職缺...")
    jobs = fetch_google_jobs()
    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    html_content = render_html(jobs, generated_at=generated_at)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(html_content, encoding="utf-8")
    # 同步輸出 google_job.html
    alt_file = output_path.parent / ("google_job.html" if output_path.name == "index.html" else "index.html")
    alt_file.write_text(html_content, encoding="utf-8")

    early_count = sum(1 for j in jobs if j.experience == "Early")
    mid_count = sum(1 for j in jobs if j.experience == "Mid (Medium)")
    print(
        f"[{datetime.now():%Y-%m-%d %H:%M:%S}] ✅ 更新完成！共 {len(jobs)} 筆 "
        f"(Early: {early_count}, Mid: {mid_count}) -> {output_path.resolve()}"
    )
    return len(jobs), early_count, mid_count


def _periodic_updater(output_path: Path, interval_seconds: int) -> None:
    """背景執行緒：每隔指定秒數自動重新抓取並更新 HTML。"""
    stop_event = threading.Event()
    while not stop_event.wait(interval_seconds):
        try:
            update_jobs_html(output_path)
        except Exception as exc:  # noqa: BLE001
            print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] ⚠️ 自動更新失敗: {exc}")


def make_handler(output_path: Path) -> type[BaseHTTPRequestHandler]:
    """建立提供 google_job.html 的 HTTP Request Handler。"""

    class JobHTMLHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            parsed_path = urllib.parse.urlparse(self.path).path
            if parsed_path in ("/", "/index.html", "/google_job.html"):
                if not output_path.exists():
                    update_jobs_html(output_path)
                content = output_path.read_bytes()
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(content)))
                self.send_header("Cache-Control", "no-cache")
                self.end_headers()
                self.wfile.write(content)
            elif parsed_path == "/healthz":
                body = b'{"status":"ok"}'
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            else:
                self.send_error(HTTPStatus.NOT_FOUND, "Not Found")

        def log_message(self, fmt: str, *args: Any) -> None:
            print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] 🌐 {self.address_string()} - {fmt % args}")

    return JobHTMLHandler


def serve_jobs(output_path: Path, host: str, port: int, interval_seconds: int) -> None:
    """啟動 HTTP 伺服器並同時啟動每日自動更新背景執行緒。"""
    try:
        update_jobs_html(output_path)
    except Exception as exc:  # noqa: BLE001
        print(f"⚠️ 初始抓取失敗 ({exc})，若已有舊檔案將先提供舊檔。")

    updater = threading.Thread(
        target=_periodic_updater,
        args=(output_path, interval_seconds),
        daemon=True,
    )
    updater.start()

    server = ThreadingHTTPServer((host, port), make_handler(output_path))
    print(
        f"🚀 Web 伺服器已啟動：http://{host}:{port}/google_job.html "
        f"(自動更新間隔：{interval_seconds} 秒)"
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n🛑 收到中斷訊號，正在關閉伺服器...")
        server.server_close()


def parse_args() -> argparse.Namespace:
    """解析命令列參數。"""
    parser = argparse.ArgumentParser(description="Google Careers 職缺爬蟲與網頁服務")
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help="輸出的 HTML 檔案路徑 (預設: google_job.html)",
    )
    parser.add_argument(
        "--serve",
        action="store_true",
        help="啟動內建 HTTP 伺服器並定期自動更新職缺",
    )
    parser.add_argument(
        "--host",
        default="0.0.0.0",
        help="HTTP 伺服器監聽位址 (預設: 0.0.0.0)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8080,
        help="HTTP 伺服器監聽連接埠 (預設: 8080)",
    )
    parser.add_argument(
        "--interval",
        type=int,
        default=DEFAULT_INTERVAL_SECONDS,
        help="自動更新間隔秒數 (預設: 86400 秒，即 24 小時)",
    )
    return parser.parse_args()


def main() -> None:
    """主程式入口。"""
    args = parse_args()
    if args.serve:
        serve_jobs(
            output_path=args.output,
            host=args.host,
            port=args.port,
            interval_seconds=args.interval,
        )
    else:
        update_jobs_html(args.output)


if __name__ == "__main__":
    main()
