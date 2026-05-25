#!/usr/bin/env python3
"""Build a daily AI news site from AI HOT public data."""

from __future__ import annotations

import json
import os
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from html import escape
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen
from xml.etree import ElementTree as ET


AIHOT_BASE_URL = "https://aihot.virxact.com"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)
BEIJING = timezone(timedelta(hours=8))

CATEGORY_LABELS = {
    "ai-models": "模型发布/更新",
    "ai-products": "产品发布/更新",
    "industry": "行业动态",
    "paper": "论文研究",
    "tip": "技巧与观点",
    None: "其他",
    "": "其他",
}
CATEGORY_ORDER = ["ai-models", "ai-products", "industry", "paper", "tip", None]


@dataclass
class NewsItem:
    title: str
    summary: str
    source: str
    url: str
    category: str | None
    published_at: str | None = None


def env(name: str, default: str) -> str:
    return os.environ.get(name, default).strip() or default


def fetch_json(path: str, params: dict[str, str] | None = None) -> dict[str, Any]:
    query = f"?{urlencode(params)}" if params else ""
    request = Request(
        f"{AIHOT_BASE_URL}{path}{query}",
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
    )
    with urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def today_beijing() -> str:
    return datetime.now(BEIJING).strftime("%Y-%m-%d")


def relative_time(value: str | None) -> str:
    dt = parse_iso(value)
    if not dt:
        return ""
    local = dt.astimezone(BEIJING)
    now = datetime.now(BEIJING)
    delta = now - local
    if timedelta(0) <= delta < timedelta(hours=1):
        minutes = max(1, int(delta.total_seconds() // 60))
        return f"{minutes} 分钟前"
    if timedelta(0) <= delta < timedelta(hours=24):
        hours = max(1, int(delta.total_seconds() // 3600))
        return f"{hours} 小时前"
    return local.strftime("%m/%d %H:%M")


def normalize_daily_item(raw: dict[str, Any], category: str | None) -> NewsItem:
    return NewsItem(
        title=str(raw.get("title") or "").strip(),
        summary=str(raw.get("summary") or "").strip(),
        source=str(raw.get("sourceName") or raw.get("source") or "").strip(),
        url=str(raw.get("sourceUrl") or raw.get("url") or "").strip(),
        category=category,
        published_at=raw.get("publishedAt"),
    )


def normalize_selected_item(raw: dict[str, Any]) -> NewsItem:
    return NewsItem(
        title=str(raw.get("title") or raw.get("title_en") or "").strip(),
        summary=str(raw.get("summary") or "").strip(),
        source=str(raw.get("source") or "").strip(),
        url=str(raw.get("url") or "").strip(),
        category=raw.get("category"),
        published_at=raw.get("publishedAt"),
    )


def load_daily_or_fallback(hours: int, take: int) -> tuple[str, str, list[NewsItem], str]:
    try:
        data = fetch_json("/api/public/daily")
        date = str(data.get("date") or today_beijing())
        lead = data.get("lead") or {}
        lead_text = str(lead.get("leadParagraph") or lead.get("title") or "").strip()
        items: list[NewsItem] = []
        for section in data.get("sections") or []:
            label = str(section.get("label") or "")
            category = category_from_label(label)
            for raw in section.get("items") or []:
                item = normalize_daily_item(raw, category)
                if item.title and item.url:
                    items.append(item)
        return date, lead_text, items[:take], "daily"
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as exc:
        print(f"Daily feed unavailable, falling back to selected items: {exc}", file=sys.stderr)
        return load_selected(hours, take)


def load_selected(hours: int, take: int) -> tuple[str, str, list[NewsItem], str]:
    since = (datetime.now(timezone.utc) - timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%SZ")
    data = fetch_json(
        "/api/public/items",
        {"mode": "selected", "since": since, "take": str(take)},
    )
    items = [normalize_selected_item(raw) for raw in data.get("items") or []]
    items = [item for item in items if item.title and item.url]
    lead = f"过去 {hours} 小时精选 AI 动态，共 {len(items)} 条。"
    return today_beijing(), lead, items, "selected"


def category_from_label(label: str) -> str | None:
    for category, title in CATEGORY_LABELS.items():
        if category and title == label:
            return category
    return None


def group_items(items: list[NewsItem]) -> dict[str | None, list[NewsItem]]:
    grouped: dict[str | None, list[NewsItem]] = {key: [] for key in CATEGORY_ORDER}
    for item in items:
        key = item.category if item.category in CATEGORY_LABELS else None
        grouped.setdefault(key, []).append(item)
    return {key: value for key, value in grouped.items() if value}


def group_indexed_items(
    indexed_items: list[tuple[int, NewsItem]],
) -> dict[str | None, list[tuple[int, NewsItem]]]:
    grouped: dict[str | None, list[tuple[int, NewsItem]]] = {key: [] for key in CATEGORY_ORDER}
    for index, item in indexed_items:
        key = item.category if item.category in CATEGORY_LABELS else None
        grouped.setdefault(key, []).append((index, item))
    return {key: value for key, value in grouped.items() if value}


def render_markdown(
    date: str,
    title: str,
    lead: str,
    items: list[NewsItem],
    source: str,
    base_url: str,
    output_dir: Path,
) -> str:
    card_gallery = output_dir / "card-images" / date / "index.html"
    card_gallery_url = site_asset_url(base_url, f"card-images/{date}/") if card_gallery.exists() else ""
    lines = [
        f"# {title} {date}",
        "",
        "> 自动生成自 AI HOT。AI 摘要可能存在误差，重要信息请以原文为准。",
        "",
    ]
    if card_gallery_url:
        lines.extend([f"**视频卡片**：[查看本期 PNG 卡片]({card_gallery_url})", ""])

    indexed_items = list(enumerate(items, 1))
    featured = indexed_items[: min(4, len(indexed_items))]
    featured_indexes = {index for index, _ in featured}
    remaining = [(index, item) for index, item in indexed_items if index not in featured_indexes]

    lines.extend(["## 概览", ""])
    if lead:
        lines.extend([lead, ""])
    lines.extend([f"数据模式：{'日报' if source == 'daily' else '精选滚动资讯'}", ""])

    if featured:
        lines.extend(["### 要闻", ""])
        for index, item in featured:
            lines.append(overview_line(index, item))
        lines.append("")

    for category, category_items in group_indexed_items(remaining).items():
        lines.extend([f"### {CATEGORY_LABELS.get(category, '其他')}", ""])
        for index, item in category_items:
            lines.append(overview_line(index, item))
        lines.append("")

    lines.append("---")
    lines.append("")

    for index, item in indexed_items:
        lines.extend(render_item_detail(date, index, item, base_url, output_dir))
    return "\n".join(lines).rstrip() + "\n"


def overview_line(index: int, item: NewsItem) -> str:
    link = f" [↗]({item.url})" if item.url else ""
    return f"- {item.title}{link} `#{index}`"


def render_item_detail(
    date: str,
    index: int,
    item: NewsItem,
    base_url: str,
    output_dir: Path,
) -> list[str]:
    heading = f"## [{item.title}]({item.url}) `#{index}`" if item.url else f"## {item.title} `#{index}`"
    lines = [heading]
    if item.summary:
        lines.extend(["", f"> {item.summary}"])

    lines.append("")
    for paragraph in detail_paragraphs(item):
        lines.extend([paragraph, ""])

    card_image = card_image_url(date, index, base_url, output_dir)
    if card_image:
        lines.extend([f"![]({card_image})", ""])

    if item.url:
        lines.extend(["相关链接：", f"- [{item.url}]({item.url})", ""])
    lines.extend(["---", ""])
    return lines


def detail_paragraphs(item: NewsItem) -> list[str]:
    if not item.summary:
        source_text = f"这条信息来自 **{item.source}**。" if item.source else "AI HOT 暂未提供更长摘要。"
        return [f"{source_text} 建议打开原文确认完整细节。"]

    sentences = split_sentences(item.summary)
    if not sentences:
        return [item.summary]

    paragraphs: list[str] = []
    current = ""
    for sentence in sentences:
        if current and len(current) + len(sentence) > 120:
            paragraphs.append(current)
            current = sentence
        else:
            current = f"{current}{sentence}" if current else sentence
    if current:
        paragraphs.append(current)

    if item.source and paragraphs:
        paragraphs[0] = f"**{item.source}** 消息，{paragraphs[0]}"
    return paragraphs[:4]


def split_sentences(text: str) -> list[str]:
    chunks = re.findall(r"[^。！？!?]+[。！？!?]?", text.strip())
    return [chunk.strip() for chunk in chunks if chunk.strip()]


def card_image_url(date: str, index: int, base_url: str, output_dir: Path) -> str:
    card_dir = output_dir / "card-images" / date
    if not card_dir.exists():
        return ""
    matches = sorted(card_dir.glob(f"{index:02d}-*.png"))
    if not matches:
        return ""
    return site_asset_url(base_url, f"card-images/{date}/{matches[0].name}")


def site_asset_url(base_url: str, path: str) -> str:
    if base_url:
        return f"{base_url.rstrip('/')}/{quote(path, safe='/')}"
    return "./" + quote(path, safe="/")


def markdown_to_article_html(markdown_text: str) -> str:
    html_lines: list[str] = []
    list_type: str | None = None

    def close_list() -> None:
        nonlocal list_type
        if list_type:
            html_lines.append(f"</{list_type}>")
            list_type = None

    def open_list(tag: str) -> None:
        nonlocal list_type
        if list_type != tag:
            close_list()
            html_lines.append(f"<{tag}>")
            list_type = tag

    for raw_line in markdown_text.splitlines():
        line = raw_line.rstrip()
        if not line:
            close_list()
            continue
        if line.startswith("# "):
            close_list()
            html_lines.append(f"<h1>{inline_markdown(line[2:])}</h1>")
        elif line.startswith("## "):
            close_list()
            html_lines.append(f"<h2>{inline_markdown(line[3:])}</h2>")
        elif line.startswith("### "):
            close_list()
            html_lines.append(f"<h3>{inline_markdown(line[4:])}</h3>")
        elif line.startswith("> "):
            close_list()
            html_lines.append(f"<blockquote>{inline_markdown(line[2:])}</blockquote>")
        elif line == "---":
            close_list()
            html_lines.append("<hr>")
        elif re.match(r"^!\[[^\]]*\]\([^)]+\)$", line):
            close_list()
            match = re.match(r"^!\[([^\]]*)\]\(([^)]+)\)$", line)
            if match:
                alt, src = match.groups()
                html_lines.append(f'<p><img src="{escape(src)}" alt="{escape(alt)}"></p>')
        elif re.match(r"^\d+\. ", line):
            open_list("ol")
            item = re.sub(r"^\d+\. ", "", line)
            html_lines.append(f"<li>{inline_markdown(item)}</li>")
        elif line.startswith("- "):
            open_list("ul")
            html_lines.append(f"<li>{inline_markdown(line[2:])}</li>")
        else:
            close_list()
            text = line.strip()
            if is_url(text):
                html_lines.append(f'<p><a href="{escape(text)}">{escape(text)}</a></p>')
            else:
                html_lines.append(f"<p>{inline_markdown(text)}</p>")
    close_list()
    return "\n".join(html_lines)


def inline_markdown(text: str) -> str:
    escaped = escape(text)
    escaped = re.sub(r"`([^`]+)`", r"<code>\1</code>", escaped)
    escaped = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", escaped)
    escaped = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r'<a href="\2">\1</a>', escaped)
    return escaped


def is_url(text: str) -> bool:
    return text.startswith("http://") or text.startswith("https://")


def html_shell(page_title: str, body: str, site_title: str, base_url: str) -> str:
    rss_link = f'<link rel="alternate" type="application/rss+xml" href="{base_url.rstrip("/")}/rss.xml">'
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{escape(page_title)}</title>
  {rss_link if base_url else ""}
  <style>
    :root {{
      color-scheme: light dark;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      line-height: 1.65;
    }}
    body {{
      max-width: 860px;
      margin: 0 auto;
      padding: 40px 20px 72px;
      background: #fbfbf8;
      color: #1f2933;
    }}
    a {{ color: #0f766e; }}
    h1 {{ font-size: 32px; line-height: 1.25; margin-bottom: 16px; }}
    h2 {{ margin-top: 36px; border-top: 1px solid #d9ded8; padding-top: 20px; }}
    h3 {{ margin: 24px 0 8px; }}
    img {{ max-width: 100%; height: auto; border-radius: 8px; }}
    hr {{ border: 0; border-top: 1px solid #d9ded8; margin: 32px 0; }}
    blockquote {{
      margin: 20px 0;
      padding: 12px 16px;
      border-left: 4px solid #14b8a6;
      background: #eef9f6;
    }}
    li {{ margin: 16px 0; }}
    .nav {{ margin-bottom: 28px; color: #52606d; }}
    .nav a {{ margin-right: 16px; }}
    .archive li {{ margin: 8px 0; }}
    @media (prefers-color-scheme: dark) {{
      body {{ background: #111827; color: #e5e7eb; }}
      blockquote {{ background: #132f2d; }}
      h2, hr {{ border-color: #374151; }}
      a {{ color: #5eead4; }}
    }}
  </style>
</head>
<body>
  <nav class="nav"><a href="./">{escape(site_title)}</a><a href="./rss.xml">RSS</a></nav>
{body}
</body>
</html>
"""


def article_url(base_url: str, date: str) -> str:
    if not base_url:
        return f"{date}.html"
    return f"{base_url.rstrip('/')}/{date}.html"


def write_article(site_title: str, base_url: str, date: str, markdown_text: str, output_dir: Path) -> None:
    body = markdown_to_article_html(markdown_text)
    card_gallery = output_dir / "card-images" / date / "index.html"
    if card_gallery.exists():
        body += (
            "\n<section>\n"
            "<h2>视频卡片</h2>\n"
            f'<p><a href="./card-images/{escape(date)}/">查看本期 PNG 卡片</a></p>\n'
            "</section>\n"
        )
    html = html_shell(f"{site_title} {date}", body, site_title, base_url)
    (output_dir / f"{date}.html").write_text(html, encoding="utf-8")


def write_index(site_title: str, base_url: str, backup_dir: Path, output_dir: Path) -> None:
    entries = sorted(backup_dir.glob("*.md"), reverse=True)
    items = []
    for path in entries[:60]:
        date = path.stem
        href = f"{date}.html"
        card_gallery = output_dir / "card-images" / date / "index.html"
        card_link = f' · <a href="card-images/{escape(date)}/">卡片</a>' if card_gallery.exists() else ""
        items.append(f'<li><a href="{href}">{escape(site_title)} {escape(date)}</a>{card_link}</li>')
    body = f"<h1>{escape(site_title)}</h1>\n<ul class=\"archive\">\n" + "\n".join(items) + "\n</ul>"
    (output_dir / "index.html").write_text(
        html_shell(site_title, body, site_title, base_url),
        encoding="utf-8",
    )


def write_cards(date: str, items: list[NewsItem], cards_dir: Path) -> None:
    payload = {
        "date": date,
        "items": [
            {
                "title": item.title,
                "summary": item.summary,
                "source": item.source,
                "url": item.url,
                "category": CATEGORY_LABELS.get(item.category, "其他"),
                "publishedAt": item.published_at,
            }
            for item in items
        ],
    }
    (cards_dir / f"{date}.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def write_rss(site_title: str, author: str, base_url: str, backup_dir: Path, output_dir: Path) -> None:
    rss = ET.Element("rss", version="2.0")
    channel = ET.SubElement(rss, "channel")
    ET.SubElement(channel, "title").text = site_title
    ET.SubElement(channel, "link").text = base_url or "."
    ET.SubElement(channel, "description").text = f"{site_title} 自动 RSS"
    ET.SubElement(channel, "language").text = "zh-CN"
    ET.SubElement(channel, "lastBuildDate").text = format_datetime(datetime.now(timezone.utc))
    for path in sorted(backup_dir.glob("*.md"), reverse=True)[:30]:
        date = path.stem
        markdown_text = path.read_text(encoding="utf-8")
        description = first_summary(markdown_text)
        item = ET.SubElement(channel, "item")
        ET.SubElement(item, "title").text = f"{site_title} {date}"
        ET.SubElement(item, "link").text = article_url(base_url, date)
        ET.SubElement(item, "guid").text = article_url(base_url, date)
        ET.SubElement(item, "author").text = author
        ET.SubElement(item, "description").text = description
        ET.SubElement(item, "pubDate").text = format_datetime(date_to_datetime(date))
    tree = ET.ElementTree(rss)
    ET.indent(tree, space="  ")
    tree.write(output_dir / "rss.xml", encoding="utf-8", xml_declaration=True)


def first_summary(markdown_text: str) -> str:
    for line in markdown_text.splitlines():
        clean = line.strip()
        if not clean:
            continue
        if (
            clean.startswith("#")
            or clean.startswith(">")
            or clean.startswith("- ")
            or clean.startswith("!")
            or clean.startswith("**视频卡片**")
            or clean.startswith("数据模式")
            or clean == "---"
            or clean == "相关链接："
            or is_url(clean)
            or re.match(r"^\d+\. ", clean)
        ):
            continue
        if clean:
            return clean[:280]
    return "AI 早报"


def date_to_datetime(value: str) -> datetime:
    try:
        date = datetime.strptime(value, "%Y-%m-%d")
    except ValueError:
        return datetime.now(timezone.utc)
    return date.replace(tzinfo=BEIJING).astimezone(timezone.utc)


def main() -> int:
    site_title = env("SITE_TITLE", "我的 AI 早报")
    author = env("AUTHOR_NAME", "AI Daily")
    base_url = env("BASE_URL", "")
    source = env("AIHOT_SOURCE", "daily").lower()
    take = int(env("AIHOT_TAKE", "30"))
    hours = int(env("AIHOT_HOURS", "24"))

    backup_dir = Path(env("BACKUP_DIR", "BACKUP"))
    output_dir = Path(env("OUTPUT_DIR", "public"))
    cards_dir = Path(env("CARDS_DIR", "cards"))
    for directory in (backup_dir, output_dir, cards_dir):
        directory.mkdir(parents=True, exist_ok=True)

    if source == "selected":
        date, lead, items, source_used = load_selected(hours, take)
    else:
        date, lead, items, source_used = load_daily_or_fallback(hours, take)

    markdown_text = render_markdown(date, site_title, lead, items, source_used, base_url, output_dir)
    (backup_dir / f"{date}.md").write_text(markdown_text, encoding="utf-8")
    write_article(site_title, base_url, date, markdown_text, output_dir)
    write_index(site_title, base_url, backup_dir, output_dir)
    write_rss(site_title, author, base_url, backup_dir, output_dir)
    write_cards(date, items, cards_dir)

    print(f"Built {date}: {len(items)} items from {source_used}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
