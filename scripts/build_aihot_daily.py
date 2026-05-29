#!/usr/bin/env python3
"""Build a daily AI news site from AI HOT public data."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from html import escape, unescape as html_unescape
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urljoin, urlparse
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
LLM_DEFAULT_BASE_URL = "https://api.deepseek.com"
LLM_DEFAULT_MODEL = "deepseek-v4-flash"
RADAR_DEFAULT_URL = "https://learnprompt.github.io/ai-news-radar/data/latest-24h.json"
RADAR_CATEGORY_MAP = {
    "model_release": "ai-models",
    "ai_product_update": "ai-products",
    "developer_tool": "tip",
    "agent_workflow": "tip",
    "research_paper": "paper",
    "infra_compute": "industry",
    "industry_business": "industry",
    "curated_hotlist": "industry",
    "ai_general": "industry",
}
DOMESTIC_SOURCE_MARKERS = {
    "36kr",
    "aibase",
    "aibase.com",
    "alibaba",
    "alibaba_qwen",
    "baidu",
    "baichuan",
    "bilibili",
    "cnblogs",
    "csdn",
    "deepseek",
    "doubao",
    "geekpark",
    "ithome",
    "ithome.com",
    "jiqizhixin",
    "juejin",
    "leiphone",
    "mp.weixin",
    "oschina",
    "qbitai",
    "qbitai.com",
    "qianzhan",
    "qwen",
    "qq.com",
    "sensetime",
    "sina",
    "sohu",
    "tencent",
    "toutiao",
    "weibo",
    "weixin",
    "xiaohongshu",
    "zhihu",
    "机器之心",
    "量子位",
    "新智元",
    "爱范儿",
    "阿里",
    "百度",
    "百川",
    "豆包",
    "商汤",
    "腾讯",
    "智谱",
    "少数派",
    "钛媒体",
}
BLOCKED_TOPIC_KEYWORDS = {
    "religion": [
        "pope",
        "vatican",
        "leo xiv",
        "encyclical",
        "church",
        "religion",
        "宗教",
        "教皇",
        "梵蒂冈",
        "通谕",
        "教会",
    ],
    "war": [
        "war",
        "warfare",
        "military",
        "defense",
        "weapon",
        "weapons",
        "drone strike",
        "army",
        "navy",
        "air force",
        "战争",
        "军事",
        "军方",
        "国防",
        "武器",
        "战场",
        "作战",
    ],
    "law": [
        "law",
        "legal",
        "lawsuit",
        "court",
        "judge",
        "attorney",
        "regulation",
        "regulator",
        "copyright",
        "antitrust",
        "government",
        "policy",
        "法律",
        "法规",
        "诉讼",
        "起诉",
        "法院",
        "法庭",
        "法官",
        "监管",
        "政策",
        "版权",
        "反垄断",
        "政府",
    ],
}


@dataclass
class NewsItem:
    title: str
    summary: str
    source: str
    url: str
    category: str | None
    published_at: str | None = None
    score: int = 0
    why_it_matters: str = ""
    background: str = ""
    details: list[str] = field(default_factory=list)
    key_facts: list[str] = field(default_factory=list)
    impact: str = ""
    source_note: str = ""
    images: list[str] = field(default_factory=list)


def env(name: str, default: str) -> str:
    return os.environ.get(name, default).strip() or default


def load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        name = name.strip().lstrip("\ufeff")
        value = value.strip().strip('"').strip("'")
        if name and name not in os.environ:
            os.environ[name] = value


def secret_env(*names: str) -> str:
    for name in names:
        value = os.environ.get(name, "").strip()
        if value:
            return value
    return ""


def bool_env(name: str, default: bool) -> bool:
    value = os.environ.get(name, "").strip().lower()
    if not value:
        return default
    return value in {"1", "true", "yes", "on", "auto"}


def int_env(name: str, default: int) -> int:
    try:
        return int(env(name, str(default)))
    except ValueError:
        return default


def extract_image_urls(raw: dict[str, Any]) -> list[str]:
    image_fields = {
        "image",
        "imageUrl",
        "image_url",
        "cover",
        "coverUrl",
        "cover_url",
        "thumbnail",
        "thumbnailUrl",
        "thumbnail_url",
        "ogImage",
        "og_image",
        "twitterImage",
        "twitter_image",
        "media",
        "images",
    }
    candidates: list[str] = []
    for key in image_fields:
        if key in raw:
            collect_image_candidates(raw.get(key), candidates)
    return dedupe_urls(candidates)


def collect_image_candidates(value: Any, output: list[str]) -> None:
    if isinstance(value, str):
        clean = value.strip()
        if clean.startswith("http://") or clean.startswith("https://"):
            output.append(clean)
        return
    if isinstance(value, list):
        for item in value:
            collect_image_candidates(item, output)
        return
    if isinstance(value, dict):
        for key in ("url", "src", "image", "imageUrl", "image_url", "large", "original"):
            if key in value:
                collect_image_candidates(value.get(key), output)


def dedupe_urls(urls: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for url in urls:
        key = normalize_url_key(url)
        if not key or key in seen:
            continue
        seen.add(key)
        result.append(url)
    return result


def fetch_json(path: str, params: dict[str, str] | None = None) -> dict[str, Any]:
    query = f"?{urlencode(params)}" if params else ""
    request = Request(
        f"{AIHOT_BASE_URL}{path}{query}",
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
    )
    with urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def load_json_source(source: str, timeout: int = 60) -> dict[str, Any]:
    if source.startswith("http://") or source.startswith("https://"):
        return fetch_url_json(source, timeout)
    return json.loads(Path(source).read_text(encoding="utf-8-sig"))


def fetch_url_json(url: str, timeout: int = 60) -> dict[str, Any]:
    request = Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    with urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def enrich_source_images(date: str, items: list[NewsItem], data_dir: Path) -> None:
    if not bool_env("FETCH_SOURCE_IMAGES", True):
        return
    max_items = int_env("SOURCE_IMAGE_MAX_ITEMS", 20)
    per_item = int_env("SOURCE_IMAGE_PER_ITEM", 3)
    timeout = int_env("SOURCE_IMAGE_TIMEOUT", 8)
    cache = load_source_image_cache(date, data_dir)
    changed = False

    for item in items[:max_items]:
        if len(item.images) >= per_item or not is_url(item.url):
            item.images = dedupe_urls(item.images)[:per_item]
            continue

        cache_key = normalize_url_key(item.url)
        cached = cache.get(cache_key)
        if isinstance(cached, list):
            item.images = dedupe_urls(item.images + [str(url) for url in cached])[:per_item]
            continue

        try:
            found = fetch_source_image_urls(item.url, timeout=timeout)
        except (HTTPError, URLError, TimeoutError, UnicodeError, ValueError) as exc:
            print(f"Source image skipped: {item.url} ({exc})", file=sys.stderr)
            found = []

        cache[cache_key] = found
        changed = True
        item.images = dedupe_urls(item.images + found)[:per_item]

    if changed:
        save_source_image_cache(date, data_dir, cache)


def capture_source_screenshots(
    date: str,
    items: list[NewsItem],
    base_url: str,
    output_dir: Path,
    data_dir: Path,
) -> None:
    mode = os.environ.get("CAPTURE_SOURCE_SCREENSHOTS", "").strip().lower()
    if mode not in {"1", "true", "yes", "on", "auto", "missing", "all"}:
        return
    script_path = Path(__file__).with_name("capture_source_screenshots.cjs")
    if not script_path.exists():
        return

    max_items = int_env("SOURCE_SCREENSHOT_MAX_ITEMS", 12)
    timeout = int_env("SOURCE_SCREENSHOT_TIMEOUT_MS", 18_000)
    screenshot_dir = output_dir / "source-images" / date
    screenshot_dir.mkdir(parents=True, exist_ok=True)

    spec_items: list[dict[str, Any]] = []
    for index, item in enumerate(items, 1):
        if len(spec_items) >= max_items:
            break
        if not is_url(item.url):
            continue
        if should_skip_screenshot(item.url):
            item.images = [
                image for image in item.images if f"/source-images/{date}/" not in image
            ]
            continue
        if item.images and mode != "all":
            continue

        file_name = f"{index:02d}-{slugify(item.title)}.png"
        screenshot_path = screenshot_dir / file_name
        public_url = site_asset_url(base_url, f"source-images/{date}/{file_name}")
        if screenshot_path.exists():
            item.images = dedupe_urls(item.images + [public_url])
            continue
        spec_items.append(
            {
                "index": index,
                "title": item.title,
                "url": item.url,
                "fileName": file_name,
            }
        )

    if not spec_items:
        return

    cache_dir = data_dir / "source-images"
    cache_dir.mkdir(parents=True, exist_ok=True)
    spec_path = cache_dir / f"{date}.screenshots.input.json"
    result_path = cache_dir / f"{date}.screenshots.result.json"
    spec_path.write_text(
        json.dumps(
            {
                "date": date,
                "outputDir": str(screenshot_dir.resolve()),
                "timeoutMs": timeout,
                "items": spec_items,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    env_vars = os.environ.copy()
    sibling_playwright = Path.cwd().parent / "juya-news-card" / "node_modules" / "playwright"
    if "PLAYWRIGHT_PACKAGE_PATH" not in env_vars and sibling_playwright.exists():
        env_vars["PLAYWRIGHT_PACKAGE_PATH"] = str(sibling_playwright)

    try:
        completed = subprocess.run(
            ["node", str(script_path), str(spec_path), str(result_path)],
            cwd=Path.cwd(),
            env=env_vars,
            capture_output=True,
            text=True,
            timeout=max(30, int(timeout / 1000) * max(1, len(spec_items)) + 20),
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        print(f"Source screenshots skipped: {exc}", file=sys.stderr)
        return

    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).strip()[:800]
        print(f"Source screenshots skipped: {detail}", file=sys.stderr)
        return

    try:
        results = json.loads(result_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return

    by_index = {index: item for index, item in enumerate(items, 1)}
    for result in results.get("items") or []:
        if not result.get("ok"):
            continue
        try:
            index = int(result.get("index"))
        except (TypeError, ValueError):
            continue
        item = by_index.get(index)
        file_name = str(result.get("fileName") or "").strip()
        if item and file_name:
            item.images = dedupe_urls(item.images + [site_asset_url(base_url, f"source-images/{date}/{file_name}")])


def should_skip_screenshot(url: str) -> bool:
    lowered = url.lower()
    blocked_hosts = [
        "://x.com/",
        "://www.x.com/",
        "://twitter.com/",
        "://www.twitter.com/",
    ]
    return any(host in lowered for host in blocked_hosts)


def load_source_image_cache(date: str, data_dir: Path) -> dict[str, list[str]]:
    path = data_dir / "source-images" / f"{date}.json"
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    if not isinstance(data, dict):
        return {}
    return {str(key): [str(url) for url in value] for key, value in data.items() if isinstance(value, list)}


def save_source_image_cache(date: str, data_dir: Path, cache: dict[str, list[str]]) -> None:
    cache_dir = data_dir / "source-images"
    cache_dir.mkdir(parents=True, exist_ok=True)
    (cache_dir / f"{date}.json").write_text(
        json.dumps(cache, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def fetch_source_image_urls(url: str, timeout: int = 8) -> list[str]:
    request = Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml",
        },
    )
    with urlopen(request, timeout=timeout) as response:
        html = response.read(600_000).decode("utf-8", errors="replace")
    return parse_meta_images(html, url)


def parse_meta_images(html: str, base_url: str) -> list[str]:
    images: list[str] = []
    target_names = {"og:image", "og:image:url", "og:image:secure_url", "twitter:image", "twitter:image:src"}
    for tag in re.findall(r"<meta\b[^>]*>", html, flags=re.IGNORECASE):
        attrs = parse_html_attrs(tag)
        name = (attrs.get("property") or attrs.get("name") or "").lower()
        content = attrs.get("content") or ""
        if name in target_names and content:
            images.append(urljoin(base_url, html_unescape(content.strip())))

    for tag in re.findall(r"<link\b[^>]*>", html, flags=re.IGNORECASE):
        attrs = parse_html_attrs(tag)
        rel = (attrs.get("rel") or "").lower()
        href = attrs.get("href") or ""
        if "image_src" in rel and href:
            images.append(urljoin(base_url, html_unescape(href.strip())))
    return dedupe_urls([url for url in images if is_url(url)])


def parse_html_attrs(tag: str) -> dict[str, str]:
    attrs: dict[str, str] = {}
    for match in re.finditer(r"([:\w-]+)\s*=\s*(['\"])(.*?)\2", tag, flags=re.DOTALL):
        attrs[match.group(1).lower()] = html_unescape(match.group(3))
    return attrs


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
        images=extract_image_urls(raw),
    )


def normalize_selected_item(raw: dict[str, Any]) -> NewsItem:
    return NewsItem(
        title=str(raw.get("title") or raw.get("title_en") or "").strip(),
        summary=str(raw.get("summary") or "").strip(),
        source=str(raw.get("source") or "").strip(),
        url=str(raw.get("url") or "").strip(),
        category=raw.get("category"),
        published_at=raw.get("publishedAt"),
        images=extract_image_urls(raw),
    )


def normalize_radar_item(raw: dict[str, Any]) -> NewsItem:
    title = str(raw.get("title_zh") or raw.get("title_bilingual") or raw.get("title") or "").strip()
    source = str(raw.get("source") or raw.get("site_name") or "").strip()
    site_name = str(raw.get("site_name") or "").strip()
    ai_score = raw.get("ai_score")
    try:
        score = int(float(ai_score) * 100)
    except (TypeError, ValueError):
        score = 0

    label = str(raw.get("ai_label") or "").strip()
    score_text = f"{score}/100" if score else "未标分"
    source_note = f"AI News Radar：{site_name or source}，相关性 {score_text}，标签 {label or '未分类'}。"
    summary = source_note if bool_env("SHOW_SOURCE_NOTES", False) else ""
    return NewsItem(
        title=title,
        summary=summary,
        source=source or site_name,
        url=str(raw.get("url") or "").strip(),
        category=RADAR_CATEGORY_MAP.get(label, "industry"),
        published_at=raw.get("published_at"),
        score=score,
        source_note=source_note,
        images=extract_image_urls(raw),
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


def load_radar(hours: int, take: int) -> tuple[str, str, list[NewsItem], str]:
    url = env("RADAR_URL", RADAR_DEFAULT_URL)
    threshold = float(env("RADAR_MIN_SCORE", "0.65"))
    data = load_json_source(url, timeout=int(env("RADAR_TIMEOUT", "90")))
    generated = parse_iso(data.get("generated_at"))
    date = generated.astimezone(BEIJING).strftime("%Y-%m-%d") if generated else today_beijing()
    window_hours = int(data.get("window_hours") or hours)
    raw_items = data.get("items") or []
    items = []
    for raw in raw_items:
        try:
            ai_score = float(raw.get("ai_score") or 0)
        except (TypeError, ValueError):
            ai_score = 0
        if raw.get("ai_is_related") is False or ai_score < threshold:
            continue
        item = normalize_radar_item(raw)
        if item.title and item.url:
            items.append(item)

    items = dedupe_items(items)
    items.sort(key=radar_rank_key, reverse=True)
    lead = f"过去 {window_hours} 小时的 AI 动态集中在模型、产品、开发工具、研究和行业融资等方向。"
    return date, lead, items[:take], "radar"


def load_hybrid(hours: int, take: int) -> tuple[str, str, list[NewsItem], str]:
    date, aihot_lead, aihot_items, aihot_source = load_daily_or_fallback(hours, take)
    radar_items: list[NewsItem] = []
    radar_lead = ""
    try:
        _, radar_lead, radar_items, _ = load_radar(hours, int(env("RADAR_TAKE", "80")))
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError, ValueError) as exc:
        print(f"AI News Radar unavailable, using AI HOT only: {exc}", file=sys.stderr)

    items = dedupe_items(aihot_items + radar_items)
    if radar_items:
        lead = f"过去 {hours} 小时的 AI 动态集中在模型升级、产品发布、开发工具、研究进展和行业融资等方向。"
    else:
        lead = aihot_lead
    return date, lead, items[: max(take, len(aihot_items))], f"hybrid-{aihot_source}"


def filter_items_for_policy(items: list[NewsItem]) -> list[NewsItem]:
    filtered = dedupe_items(items)
    if bool_env("FOREIGN_SOURCES_ONLY", False):
        filtered = [item for item in filtered if is_foreign_source(item)]
    blocked_topics = env("EXCLUDE_TOPICS", "")
    if blocked_topics:
        filtered = [item for item in filtered if not has_blocked_topic(item, blocked_topics)]
    return filtered


def is_foreign_source(item: NewsItem) -> bool:
    host = urlparse(item.url).netloc.lower().lstrip("www.")
    if host.endswith(".cn") or ".com.cn" in host or ".cn/" in item.url.lower():
        return False
    if any(marker in host for marker in DOMESTIC_SOURCE_MARKERS):
        return False
    return bool(host)


def has_blocked_topic(item: NewsItem, topic_spec: str) -> bool:
    topics = [topic.strip().lower() for topic in re.split(r"[,，\s]+", topic_spec) if topic.strip()]
    keyword_groups: list[str] = []
    for topic in topics:
        if topic in BLOCKED_TOPIC_KEYWORDS:
            keyword_groups.extend(BLOCKED_TOPIC_KEYWORDS[topic])
        else:
            keyword_groups.append(topic)
    haystack = " ".join(
        [
            item.title,
            item.summary,
            item.background,
            item.why_it_matters,
            item.impact,
            item.source_note,
            item.source,
            item.url,
        ]
        + item.details
        + item.key_facts
    ).lower()
    return any(keyword.lower() in haystack for keyword in keyword_groups)


def dedupe_items(items: list[NewsItem]) -> list[NewsItem]:
    seen_urls: set[str] = set()
    seen_titles: set[str] = set()
    deduped: list[NewsItem] = []
    for item in items:
        url_key = normalize_url_key(item.url)
        title_key = normalize_title_key(item.title)
        if url_key and url_key in seen_urls:
            continue
        if title_key and title_key in seen_titles:
            continue
        if url_key:
            seen_urls.add(url_key)
        if title_key:
            seen_titles.add(title_key)
        deduped.append(item)
    return deduped


def normalize_url_key(url: str) -> str:
    return re.sub(r"[?#].*$", "", url.strip().lower()).rstrip("/")


def normalize_title_key(title: str) -> str:
    return re.sub(r"\s+", "", title.strip().lower())


def radar_rank_key(item: NewsItem) -> tuple[int, float]:
    published = parse_iso(item.published_at)
    timestamp = published.timestamp() if published else 0
    source_bonus = 8 if source_tier(item).startswith("A") else 0
    return item.score + source_bonus, timestamp


def enrich_with_llm(date: str, lead: str, items: list[NewsItem]) -> tuple[str, list[NewsItem]]:
    api_key = secret_env("LLM_API_KEY", "DEEPSEEK_API_KEY")
    enabled = bool_env("ENRICH_WITH_LLM", bool(api_key))
    if not enabled or not api_key or not items:
        return lead, items

    input_limit = int(env("LLM_INPUT_ITEMS", "30"))
    output_limit = int(env("ENRICH_MAX_ITEMS", "20"))
    attempts = [
        (input_limit, output_limit),
        (min(input_limit, 30), min(output_limit, 12)),
    ]
    response: dict[str, Any] = {}
    enriched: list[NewsItem] = []
    last_error: Exception | None = None
    for attempt_input_limit, attempt_output_limit in attempts:
        input_items = items[: max(1, attempt_input_limit)]
        payload = build_llm_payload(date, lead, input_items, attempt_output_limit)
        try:
            response = call_llm(api_key, payload)
            enriched = apply_llm_enrichment(items, response)
            if enriched:
                output_limit = attempt_output_limit
                break
        except Exception as exc:  # Keep the daily pipeline alive if the model/API fails.
            last_error = exc

    if last_error and not enriched:
        print(f"LLM enrichment skipped: {last_error}", file=sys.stderr)
        return lead, items

    if not enriched:
        print("LLM enrichment returned no usable items; using original items.", file=sys.stderr)
        return lead, items

    new_lead = str(response.get("lead") or lead).strip()
    print(f"LLM enrichment enabled: {len(enriched)} items selected.")
    return new_lead or lead, enriched[:output_limit]


def build_llm_payload(
    date: str,
    lead: str,
    items: list[NewsItem],
    output_limit: int,
) -> dict[str, Any]:
    source_items = [
        {
            "index": index,
            "title": item.title,
            "summary": item.summary if bool_env("LLM_USE_SUMMARY", True) else "",
            "source_domain": urlparse(item.url).netloc.lower().lstrip("www."),
            "source_tier_hint": source_tier(item),
            "category": item.category,
            "published_at": item.published_at,
            "radar_or_prior_score": item.score,
            "has_image": bool(item.images),
        }
        for index, item in enumerate(items, 1)
    ]
    system = (
        "你是中文 AI 早报资料编辑。你的任务是把候选新闻整理成接近 Juya BACKUP 的长文字版素材，"
        "用于信息归档和后续人工编辑，不是公众号排版、不是卡片文案、也不是视频脚本。"
        "主要基于输入里的标题扩写，可参考摘要里的显式事实；不要编造参数、价格、日期、融资额、公司表态或输入未给出的细节。"
        "写法要像资讯资料包：短句、强事实、少评论、少泛化判断。"
        "正文不要出现网址、链接、信源备注、来源说明、网站名，也不要写“原文/来源/据某媒体”。"
        "不要复述输入导语里的生产工具名、候选条数、信源数量或采集方式。"
        "不要选择或扩写宗教、战争、法律、监管、诉讼、政策类内容。"
        "禁止使用“全球最大”“里程碑”“重塑格局”“迫使对手跟进”等没有输入依据的强判断；影响分析必须保守，并注明有待核验。"
        "遇到信息不足，就只做基于标题的保守解释，不要提醒读者去原文核对。只返回 JSON，不要 Markdown，不要解释。"
    )
    user = {
        "date": date,
        "lead": lead,
        "selection_rule": {
            "max_items": output_limit,
            "prefer": ["官方源", "重大模型/产品发布", "影响开发者或创作者的变化", "安全风险", "可操作技巧"],
            "avoid": ["重复事件", "纯营销口号", "国内网站来源", "宗教", "战争", "法律", "监管", "诉讼", "政策"],
        },
        "output_schema": {
            "lead": "100-180字中文导语，概括今日 AI 动态主线",
            "items": [
                {
                    "index": "必须使用输入里的 index",
                    "title": "可轻微润色，但不要改事实",
                    "summary": "一句话加粗摘要素材，50-110字，直接说明发生了什么、关键数字或功能",
                    "score": "1-100的重要性分",
                    "background": "第一段事实背景，60-130字；不要写空泛评价",
                    "details": ["3-6段正文事实，每段45-110字；优先写参数、价格、版本、功能、时间；只写输入能支持的内容"],
                    "why_it_matters": "可选：1段看点，60-140字；没有足够依据就留空",
                    "key_facts": ["可选：2-4条关键事实，每条不超过60字；不要重复正文"],
                    "impact": "可选：1段后续影响，60-140字；避免夸张预测",
                }
            ],
        },
        "items": source_items,
    }
    return {"messages": [{"role": "system", "content": system}, {"role": "user", "content": json.dumps(user, ensure_ascii=False)}]}


def call_llm(api_key: str, payload: dict[str, Any]) -> dict[str, Any]:
    base_url = env("LLM_BASE_URL", LLM_DEFAULT_BASE_URL).rstrip("/")
    model = env("LLM_MODEL", LLM_DEFAULT_MODEL)
    timeout = int(env("LLM_TIMEOUT", "90"))
    body = {
        "model": model,
        "messages": payload["messages"],
        "temperature": float(env("LLM_TEMPERATURE", "0.2")),
        "max_tokens": int(env("LLM_MAX_TOKENS", "6000")),
    }
    if bool_env("LLM_JSON_RESPONSE_FORMAT", True):
        body["response_format"] = {"type": "json_object"}
    request = Request(
        f"{base_url}/chat/completions",
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": USER_AGENT,
        },
        method="POST",
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            data = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:500]
        raise RuntimeError(f"LLM API HTTP {exc.code}: {detail}") from exc

    choices = data.get("choices") or []
    if not choices:
        raise RuntimeError("LLM API returned no choices")
    content = str((choices[0].get("message") or {}).get("content") or "").strip()
    if not content:
        raise RuntimeError("LLM API returned empty content")
    return json.loads(extract_json_object(content))


def extract_json_object(text: str) -> str:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError("No JSON object found in LLM response")
    return cleaned[start : end + 1]


def apply_llm_enrichment(original_items: list[NewsItem], response: dict[str, Any]) -> list[NewsItem]:
    by_index = {index: item for index, item in enumerate(original_items, 1)}
    enriched: list[NewsItem] = []
    seen: set[int] = set()
    for raw in response.get("items") or []:
        try:
            index = int(raw.get("index"))
        except (TypeError, ValueError):
            continue
        item = by_index.get(index)
        if not item or index in seen:
            continue
        seen.add(index)

        title = str(raw.get("title") or "").strip()
        summary = str(raw.get("summary") or "").strip()
        if title:
            item.title = title
        if summary:
            item.summary = summary
        item.score = clamp_score(raw.get("score"))
        item.background = str(raw.get("background") or "").strip()
        item.details = clean_string_list(raw.get("details"), limit=4)
        item.why_it_matters = str(raw.get("why_it_matters") or "").strip()
        item.key_facts = clean_string_list(raw.get("key_facts"))
        item.impact = str(raw.get("impact") or "").strip()
        item.source_note = str(raw.get("source_note") or "").strip()
        enriched.append(item)

    enriched.sort(key=lambda item: item.score, reverse=True)
    return enriched


def clamp_score(value: Any) -> int:
    try:
        score = int(value)
    except (TypeError, ValueError):
        return 0
    return max(0, min(100, score))


def clean_string_list(value: Any, limit: int = 4) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()][:limit]


def source_tier(item: NewsItem) -> str:
    source = f"{item.source} {item.url}".lower()
    official_markers = [
        "openai.com",
        "anthropic.com",
        "deepmind.google",
        "ai.googleblog.com",
        "microsoft.com",
        "nvidia.com",
        "meta.com",
        "huggingface.co",
        "github.com",
        "arxiv.org",
        "qwenlm.github.io",
        "deepseek.com",
    ]
    media_markers = ["techcrunch", "the verge", "bloomberg", "36kr", "机器之心", "量子位", "新智元"]
    social_markers = ["x.com", "twitter.com", "youtube.com", "bilibili.com", "reddit.com", "hacker news"]
    if any(marker in source for marker in official_markers):
        return "A 官方/一手源"
    if any(marker in source for marker in media_markers):
        return "B 媒体源"
    if any(marker in source for marker in social_markers):
        return "C 社交/社区源"
    return "D 待核验线索"


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


def build_lead_from_items(hours: int, items: list[NewsItem]) -> str:
    if not items:
        return f"过去 {hours} 小时，AI 动态较少，暂未筛出适合成稿的海外一线资讯。"
    title_snippets = [clean_title_for_lead(item.title) for item in items[:8]]
    title_text = "；".join(snippet for snippet in title_snippets if snippet)
    themes = []
    categories = {item.category for item in items}
    if "ai-models" in categories:
        themes.append("模型升级")
    if "ai-products" in categories:
        themes.append("产品发布")
    if "tip" in categories:
        themes.append("开发工具")
    if "paper" in categories:
        themes.append("研究进展")
    if "industry" in categories:
        themes.append("行业融资与平台动态")
    theme_text = "、".join(themes) if themes else "模型、产品和行业动态"
    return f"过去 {hours} 小时，AI 动态主要集中在{theme_text}。重点包括：{title_text}。"


def clean_title_for_lead(title: str) -> str:
    cleaned = re.sub(r"\s+", " ", title).strip()
    cleaned = re.sub(r"`#\d+`", "", cleaned).strip()
    return cleaned.rstrip("。")


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
    issue_url = env("ISSUE_URL", "")
    cover_image_url = env("COVER_IMAGE_URL", "")
    if not cover_image_url:
        cover_path = output_dir / "covers" / f"{date}.png"
        if cover_path.exists():
            cover_image_url = site_asset_url(base_url, f"covers/{date}.png")
    lines: list[str] = []
    if issue_url:
        lines.extend([f"# [{date}]({issue_url})", ""])
    if cover_image_url:
        lines.extend([f"![]({cover_image_url})", ""])
    lines.extend([f"# {title} {date}", ""])
    if card_gallery_url:
        lines.extend([f"**视频卡片**：[查看本期 PNG 卡片]({card_gallery_url})", ""])

    indexed_items = list(enumerate(items, 1))
    featured = indexed_items[: min(4, len(indexed_items))]
    featured_indexes = {index for index, _ in featured}
    remaining = [(index, item) for index, item in indexed_items if index not in featured_indexes]

    lines.extend(["## 概览", ""])
    if lead:
        lines.extend([lead, ""])

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


def source_label(source: str) -> str:
    if source == "daily":
        return "AI HOT 日报"
    if source == "selected":
        return "AI HOT 精选滚动资讯"
    if source == "radar":
        return "AI News Radar"
    if source.startswith("hybrid"):
        return "AI HOT + AI News Radar"
    return source


def overview_line(index: int, item: NewsItem) -> str:
    if bool_env("SHOW_ARTICLE_LINKS", False) and item.url:
        return f"- {item.title} [↗]({item.url}) `#{index}`"
    return f"- {item.title} `#{index}`"


def render_item_detail(
    date: str,
    index: int,
    item: NewsItem,
    base_url: str,
    output_dir: Path,
) -> list[str]:
    if bool_env("SHOW_ARTICLE_LINKS", False) and item.url:
        heading = f"## [{item.title}]({item.url}) `#{index}`"
    else:
        heading = f"## {item.title} `#{index}`"
    lines = [heading]
    if item.summary:
        lines.extend(["", f"> {summary_quote(item)}"])

    lines.append("")
    for paragraph in detail_paragraphs(item):
        lines.extend([paragraph, ""])

    for image in item.images:
        lines.extend([f"![]({image})", ""])

    card_image = card_image_url(date, index, base_url, output_dir)
    if card_image:
        lines.extend([f"![]({card_image})", ""])

    if bool_env("SHOW_ARTICLE_LINKS", False) and item.url:
        lines.extend(["相关链接：", f"- [{item.url}]({item.url})", ""])
    lines.extend(["---", ""])
    return lines


def summary_quote(item: NewsItem) -> str:
    summary = item.summary.strip()
    if not bool_env("SHOW_SOURCE_NAMES", False):
        return summary
    source = short_source_name(item.source)
    if not source or source.lower() in summary.lower()[:40]:
        return summary
    return f"**{source}** {summary}"


def short_source_name(source: str) -> str:
    clean = re.sub(r"（.*?）", "", source).strip()
    clean = clean.split("：", 1)[0].strip() if "：" in clean else clean
    clean = clean.split(":", 1)[0].strip() if ":" in clean else clean
    return clean[:24]


def detail_paragraphs(item: NewsItem) -> list[str]:
    if item.background or item.details or item.why_it_matters or item.key_facts or item.impact:
        paragraphs: list[str] = []
        if item.background:
            paragraphs.append(item.background)
        for detail in item.details:
            paragraphs.append(detail)
        if item.why_it_matters:
            paragraphs.append(item.why_it_matters)
        if item.key_facts and bool_env("SHOW_KEY_FACTS", False):
            facts = "；".join(item.key_facts)
            paragraphs.append(f"**关键事实：**{facts}。")
        if item.impact:
            paragraphs.append(item.impact)
        if item.source_note and bool_env("SHOW_SOURCE_NOTES", False):
            paragraphs.append(f"**信源备注：**{item.source_note}")
        return paragraphs

    if not item.summary:
        return [fallback_title_expansion(item.title)]

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

    if item.source and paragraphs and bool_env("SHOW_SOURCE_NAMES", False):
        paragraphs[0] = f"**{item.source}** 消息，{paragraphs[0]}"
    return paragraphs[:4]


def fallback_title_expansion(title: str) -> str:
    clean_title = title.strip() or "这条 AI 动态"
    return (
        f"{clean_title}。这条动态目前能确定的信息集中在标题本身：它涉及一个新的模型、产品、工具或行业变化，"
        "适合先作为今日候选要闻保留。后续成稿时可以围绕它解决了什么问题、面向谁、可能改变哪类工作流继续补充。"
    )


def split_sentences(text: str) -> list[str]:
    chunks = re.findall(r"[^。！？!?]+[。！？!?]?", text.strip())
    return [chunk.strip() for chunk in chunks if chunk.strip()]


def slugify(value: str, max_length: int = 42) -> str:
    cleaned = re.sub(r"[^\w\u4e00-\u9fff-]+", "-", value.lower(), flags=re.UNICODE)
    cleaned = re.sub(r"-+", "-", cleaned).strip("-")
    return (cleaned or "source")[:max_length].strip("-") or "source"


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
                "score": item.score,
                "background": item.background,
                "details": item.details,
                "whyItMatters": item.why_it_matters,
                "keyFacts": item.key_facts,
                "impact": item.impact,
                "sourceNote": item.source_note,
                "images": item.images,
            }
            for item in items
        ],
    }
    (cards_dir / f"{date}.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def write_enriched_data(date: str, lead: str, items: list[NewsItem], data_dir: Path) -> None:
    enriched_dir = data_dir / "enriched"
    enriched_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "date": date,
        "lead": lead,
        "items": [
            {
                "title": item.title,
                "summary": item.summary,
                "source": item.source,
                "url": item.url,
                "category": item.category,
                "publishedAt": item.published_at,
                "score": item.score,
                "background": item.background,
                "details": item.details,
                "whyItMatters": item.why_it_matters,
                "keyFacts": item.key_facts,
                "impact": item.impact,
                "sourceNote": item.source_note,
                "images": item.images,
            }
            for item in items
        ],
    }
    (enriched_dir / f"{date}.json").write_text(
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
    load_dotenv(Path(".env"))

    site_title = env("SITE_TITLE", "我的 AI 早报")
    author = env("AUTHOR_NAME", "AI Daily")
    base_url = env("BASE_URL", "")
    source = env("AIHOT_SOURCE", "daily").lower()
    take = int(env("AIHOT_TAKE", "30"))
    hours = int(env("AIHOT_HOURS", "24"))

    backup_dir = Path(env("BACKUP_DIR", "BACKUP"))
    output_dir = Path(env("OUTPUT_DIR", "public"))
    cards_dir = Path(env("CARDS_DIR", "cards"))
    data_dir = Path(env("DATA_DIR", "data"))
    for directory in (backup_dir, output_dir, cards_dir, data_dir):
        directory.mkdir(parents=True, exist_ok=True)

    if source == "selected":
        date, lead, items, source_used = load_selected(hours, take)
    elif source == "radar":
        date, lead, items, source_used = load_radar(hours, take)
    elif source == "hybrid":
        date, lead, items, source_used = load_hybrid(hours, take)
    else:
        date, lead, items, source_used = load_daily_or_fallback(hours, take)

    before_filter_count = len(items)
    items = filter_items_for_policy(items)
    if before_filter_count != len(items):
        print(f"Policy filter kept {len(items)}/{before_filter_count} items.")

    lead, items = enrich_with_llm(date, lead, items)
    items = filter_items_for_policy(items)
    if bool_env("REBUILD_LEAD_FROM_ITEMS", True):
        lead = build_lead_from_items(hours, items)
    enrich_source_images(date, items, data_dir)
    capture_source_screenshots(date, items, base_url, output_dir, data_dir)

    markdown_text = render_markdown(date, site_title, lead, items, source_used, base_url, output_dir)
    (backup_dir / f"{date}.md").write_text(markdown_text, encoding="utf-8")
    write_article(site_title, base_url, date, markdown_text, output_dir)
    write_index(site_title, base_url, backup_dir, output_dir)
    write_rss(site_title, author, base_url, backup_dir, output_dir)
    write_cards(date, items, cards_dir)
    write_enriched_data(date, lead, items, data_dir)

    print(f"Built {date}: {len(items)} items from {source_used}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
