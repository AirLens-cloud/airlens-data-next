#!/usr/bin/env python3
"""AirLens 뉴스 RSS 수집기 (Supabase-free — HF dataset 발행).

`news-aggregator` Edge Function(`apps/web/supabase/functions/news-aggregator/`)이
Supabase Storage 402(quota) 로 2026-08-16 부터 전량 실패 중이다. Supabase 는 완전
은퇴 확정이므로 그 Edge Function 을 되살리지 않고, 같은 파싱/정규화 규칙을
`collect_firms.py` 규약(테스트 가능한 단일 정본 스크립트)대로 여기로 옮긴다.

원본과의 차이 (의도적 축소 — 이번 단계 범위):
  - 이미지 캐싱(`news-images` Storage 버킷 재업로드) 제외 — 원본 `image_url` 그대로
    보존. 캐싱은 별도 후속(원본이 캐싱하는 이유는 CSP img-src 제한인데, 이번 산출물은
    아직 프론트가 소비하지 않는다).
  - NewsData.io API 보충 수집(`feeds.ts::fetchNewsDataArticles`) 제외 — RSS 가 아니라
    유료 키(`NEWSDATA_API_KEY`) 가 필요한 별도 소스라 "RSS 소스 목록·파싱" 이식
    범위 밖으로 판단. 필요해지면 이 모듈에 별 함수로 추가.
  - LLM 요약/분류(`news-llm`, `news-summarize-ko`, `blog-draft`) 제외 — 사용자가
    "요약 아니라 설명·소개 스타일 + Hermes 담당"으로 방향 전환, 별도 설계 대기 중.
    `category`/`is_top_story`/`summary_en` 은 원본 `enrichment.ts::enrichArticle`
    이 이미 순수 규칙 기반(키워드 매치/truncate)이라 LLM 이 아니다 — 그대로 이식.
    `summary_ko`/`sentiment_score`/`related_policy_id` 는 이 파이프라인 어디서도
    계산되지 않으므로(원본도 DB insert 시 채우지 않음) 채우지 않는다 — 0/빈문자열
    날조 금지, null 로 둔다(AirLens 날조 금지 원칙).

병합 시맨틱 (원본과 가장 다른 지점): 원본은 Supabase 테이블에 upsert(누적)했다.
여기는 정적 JSON 파일이 진실 소스이므로, 이 스크립트가 직접 누적을 구현한다 —
직전 발행본(`--existing`)을 읽어 `article_url` 기준 병합, 최신 N건만 유지한다
(`MAX_ARTICLES`). 이 설계 덕에 firms 수집기가 겪은 "부분 스냅샷이 이전 정상본을
통째로 덮는" 붕괴 패턴 자체가 구조적으로 없다 — 이번 실행에서 새로 못 받은 기사도
기존 파일에 남아있으면 그대로 유지된다.

수집 0건(모든 피드가 실패 또는 빈 응답)이면 파일을 쓰지 않고 즉시 실패한다
(exit 1) — 이전 데이터를 절대 덮지 않는다. `hf_publish.py` 는 이 경우 업로드할
파일 자체가 없으므로 자연히 스킵된다 (firms-collect.yml 과 동일 패턴).

네트워크: 표준 라이브러리 `urllib` 만 사용 (feeds.ts 의 fetch 와 동일하게 10s
타임아웃 + User-Agent). 피드 하나가 죽어도 나머지는 계속 수집한다 — RSS 23개
소스 중 하나의 일시 장애로 전체 cron 을 빨갛게 만들 이유가 없다(FIRMS 의
"critical 단일 API" 와 다른 성격).
"""
from __future__ import annotations

import argparse
import email.utils
import hashlib
import html
import json
import os
import pathlib
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from datetime import datetime, timezone

# html.unescape 별칭 — parse_og_image()/fetch_og_image() 가 지역명 `html` 로
# 모듈을 가리므로(섀도잉) 함수 안에서도 안전하게 부를 이름을 하나 둔다.
_unescape = html.unescape

FETCH_TIMEOUT_S = 10
MAX_ARTICLES_PER_FEED = 10  # feeds.ts::MAX_ARTICLES_PER_FEED 와 동일

# og:image 승격 — RSS 가 media:content/enclosure 없이 본문 첫 <img> 만 줄 때
# (예: airqualitynews.com) 그 이미지는 기사 대표 이미지가 아니라 본문 임베드일 수
# 있다(실사고: DEFRA 페이지 UI 스크린샷이 Dispatch 카드에 노출, 2026-09-02).
# 그런 약한 출처만 기사 페이지 og:image 로 1회 승격 시도한다. best-effort —
# 실패해도 수집은 계속된다.
OG_FETCH_TIMEOUT_S = 5
MAX_OG_FETCHES_PER_RUN = 25  # 런당 추가 HTTP 상한 (약한 이미지 피드는 소수)
OG_HTML_READ_CAP = 131072    # og:image 메타는 <head> 에 있다 — 128KB 면 충분

# 누적 상한 — 병합 후 이 수를 넘으면 published_at 오래된 것부터 잘라낸다. 기사
# 레코드 1건은 title+summary(≤500자)+url 2개+메타 필드로 대략 600~800 bytes.
# 400건 ≈ 250~320KB — 브라우저가 그대로 받는 정적 자산이라(firms 화재 레이어와
# 동일 우려) 너무 크지 않게, 그러나 6시간 주기 4회/일 누적에 여유 있게 잡는다.
MAX_ARTICLES = 400

FEEDS: list[dict[str, object]] = [
    {"name": "UNEP", "url": "https://www.unep.org/rss.xml", "site_url": "https://www.unep.org", "default_topic": "policy"},
    {"name": "WHO Air Quality", "url": "https://www.who.int/rss-feeds/news-english.xml", "site_url": "https://www.who.int", "default_topic": "health"},
    {"name": "Climate Home News", "url": "https://www.climatechangenews.com/feed/", "site_url": "https://www.climatechangenews.com", "default_topic": "policy"},
    {"name": "Clean Air Fund", "url": "https://www.cleanairfund.org/feed/", "site_url": "https://www.cleanairfund.org", "default_topic": "community"},
    {"name": "Phys.org Environment", "url": "https://phys.org/rss-feed/earth-news/environment/", "site_url": "https://phys.org", "default_topic": "technology"},
    {"name": "IQAir Newsroom", "url": "https://www.iqair.com/newsroom/rss", "site_url": "https://www.iqair.com", "default_topic": "health"},
    {"name": "Carbon Brief", "url": "https://www.carbonbrief.org/feed/", "site_url": "https://www.carbonbrief.org", "default_topic": "policy"},
    {"name": "The Guardian Environment", "url": "https://www.theguardian.com/environment/rss", "site_url": "https://www.theguardian.com", "default_topic": "community"},
    {"name": "EcoWatch", "url": "https://www.ecowatch.com/feed", "site_url": "https://www.ecowatch.com", "default_topic": "community"},
    {"name": "Environmental Health News", "url": "https://www.ehn.org/feed", "site_url": "https://www.ehn.org", "default_topic": "health"},
    {"name": "China Dialogue", "url": "https://www.chinadialogue.net/feed/", "site_url": "https://www.chinadialogue.net", "default_topic": "policy", "default_region": "asia"},
    {"name": "The Third Pole", "url": "https://www.thethirdpole.net/feed/", "site_url": "https://www.thethirdpole.net", "default_topic": "community", "default_region": "asia"},
    {"name": "US EPA", "url": "https://www.epa.gov/newsreleases/search/rss", "site_url": "https://www.epa.gov", "default_topic": "policy", "default_region": "americas"},
    {"name": "EEA", "url": "https://www.eea.europa.eu/api/rss", "site_url": "https://www.eea.europa.eu", "default_topic": "policy", "default_region": "europe"},
    {"name": "NASA Climate", "url": "https://climate.nasa.gov/news/rss.xml", "site_url": "https://climate.nasa.gov", "default_topic": "technology", "default_region": "americas"},
    {"name": "Mongabay", "url": "https://news.mongabay.com/feed/", "site_url": "https://news.mongabay.com", "default_topic": "community"},
    {"name": "Mongabay India", "url": "https://india.mongabay.com/feed/", "site_url": "https://india.mongabay.com", "default_topic": "community", "default_region": "asia"},
    {"name": "Yale Environment 360", "url": "https://e360.yale.edu/feed.xml", "site_url": "https://e360.yale.edu", "default_topic": "community"},
    {"name": "Dialogue Earth", "url": "https://dialogue.earth/en/feed/", "site_url": "https://dialogue.earth", "default_topic": "policy"},
    {"name": "Eco-Business", "url": "https://www.eco-business.com/feeds/news/", "site_url": "https://www.eco-business.com", "default_topic": "community", "default_region": "asia"},
    {"name": "AllAfrica Environment", "url": "https://allafrica.com/tools/headlines/rdf/environment/headlines.rdf", "site_url": "https://allafrica.com", "default_topic": "community", "default_region": "africa"},
    {"name": "Air Quality News", "url": "https://airqualitynews.com/feed/", "site_url": "https://airqualitynews.com", "default_topic": "health", "default_region": "europe"},
]

REGION_KEYWORDS: dict[str, list[str]] = {
    "asia": ["china", "india", "japan", "korea", "asia", "beijing", "delhi", "tokyo", "seoul", "bangkok", "singapore", "indonesia", "vietnam", "philippines", "thailand", "malaysia"],
    "europe": ["europe", "eu", "uk", "germany", "france", "london", "paris", "berlin", "brussels", "spain", "italy", "netherlands", "sweden", "norway", "poland"],
    "americas": ["usa", "america", "canada", "brazil", "mexico", "washington", "new york", "california", "latin america", "chile", "colombia", "argentina"],
    "africa": ["africa", "nigeria", "kenya", "south africa", "egypt", "ethiopia", "ghana", "tanzania", "congo"],
    "oceania": ["australia", "new zealand", "pacific", "oceania", "sydney", "melbourne"],
    "middle_east": ["middle east", "saudi", "iran", "iraq", "uae", "dubai", "qatar", "israel", "turkey"],
}

CATEGORY_KEYWORDS: dict[str, list[str]] = {
    "airlens": ["airlens", "air lens"],
    "policy": [
        "regulation", "law", "ban", "treaty", "government", "parliament", "congress",
        "epa", "legislation", "mandate", "enforcement", "compliance", "standard",
        "emission target", "court ruling", "supreme court", "executive order",
        "clean air act", "carbon tax", "net zero", "paris agreement", "cop2",
        "ministry", "decree", "ordinance", "penalty", "fine", "sanction",
    ],
    "research": [
        "study", "research", "journal", "paper", "findings", "analysis",
        "satellite", "sensor", "monitoring", "measurement", "peer-reviewed",
        "university", "scientists", "data shows", "model", "algorithm",
        "lancet", "nature", "science", "methodology", "experiment",
    ],
    "environment": [
        "pollution", "smog", "haze", "wildfire", "dust storm", "pm2.5", "pm10",
        "ozone", "aqi", "air quality index", "hazardous", "unhealthy",
        "climate", "ecosystem", "deforestation", "industrial emission",
        "traffic emission", "crop burning", "coal plant", "fossil fuel",
    ],
}

COUNTRY_KEYWORDS: dict[str, list[str]] = {
    "KR": ["korea", "korean", "seoul", "busan", "incheon"],
    "JP": ["japan", "japanese", "tokyo", "osaka"],
    "CN": ["china", "chinese", "beijing", "shanghai", "guangzhou"],
    "IN": ["india", "indian", "delhi", "mumbai", "bangalore", "kolkata"],
    "US": ["united states", "usa", "american", "washington dc", "new york", "california"],
    "GB": ["united kingdom", "british", "london", "england"],
    "DE": ["germany", "german", "berlin", "munich"],
    "FR": ["france", "french", "paris", "lyon"],
    "BR": ["brazil", "brazilian", "rio de janeiro", "brasilia"],
    "AU": ["australia", "australian", "sydney", "melbourne"],
    "TH": ["thailand", "thai", "bangkok"],
    "VN": ["vietnam", "vietnamese", "hanoi", "ho chi minh"],
    "ID": ["indonesia", "indonesian", "jakarta"],
    "PH": ["philippines", "filipino", "manila"],
    "MX": ["mexico", "mexican", "mexico city"],
    "NG": ["nigeria", "nigerian", "lagos", "abuja"],
    "ZA": ["south africa", "johannesburg", "cape town"],
    "EG": ["egypt", "egyptian", "cairo"],
    "AE": ["uae", "emirates", "dubai", "abu dhabi"],
    "SA": ["saudi", "saudi arabia", "riyadh"],
    "PK": ["pakistan", "pakistani", "lahore", "karachi", "islamabad"],
    "BD": ["bangladesh", "bangladeshi", "dhaka"],
    "NP": ["nepal", "nepali", "kathmandu"],
    "LK": ["sri lanka", "sri lankan", "colombo"],
    "MN": ["mongolia", "mongolian", "ulaanbaatar"],
    "MY": ["malaysia", "malaysian", "kuala lumpur"],
    "SG": ["singapore", "singaporean"],
    "TW": ["taiwan", "taiwanese", "taipei"],
    "IR": ["iran", "iranian", "tehran"],
    "IQ": ["iraq", "iraqi", "baghdad"],
    "TR": ["turkey", "turkish", "istanbul", "ankara"],
    "IL": ["israel", "israeli", "tel aviv", "jerusalem"],
    "QA": ["qatar", "qatari", "doha"],
    "KZ": ["kazakhstan", "almaty", "astana"],
    "UZ": ["uzbekistan", "tashkent"],
    "RU": ["russia", "russian", "moscow"],
    "UA": ["ukraine", "ukrainian", "kyiv", "kiev"],
    "PL": ["poland", "polish", "warsaw", "krakow"],
    "IT": ["italy", "italian", "rome", "milan"],
    "ES": ["spain", "spanish", "madrid", "barcelona"],
    "NL": ["netherlands", "dutch", "amsterdam"],
    "SE": ["sweden", "swedish", "stockholm"],
    "CA": ["canada", "canadian", "toronto", "vancouver"],
    "CL": ["chile", "chilean", "santiago"],
    "CO": ["colombia", "colombian", "bogota", "medellin"],
    "AR": ["argentina", "argentine", "buenos aires"],
    "PE": ["peru", "peruvian"],
    "KE": ["kenya", "kenyan", "nairobi"],
    "GH": ["ghana", "ghanaian", "accra"],
    "ET": ["ethiopia", "ethiopian", "addis ababa"],
    "TZ": ["tanzania", "tanzanian", "dar es salaam"],
    "UG": ["uganda", "ugandan", "kampala"],
    "MA": ["morocco", "moroccan", "casablanca", "rabat"],
}

STRONG_TOP_STORY_SIGNALS = (
    "who ", "unep", "epa", "breakthrough", "first ever", "historic",
    "banned", "declared", "emergency", "crisis level", "all-time",
    "court ruling", "supreme court", "parliament passes", "congress passes",
)

_CDATA_RE = re.compile(r"<!\[CDATA\[([\s\S]*?)\]\]>")
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")
_ITEM_RE = re.compile(r"<(item|entry)[\s>].*?</\1>", re.DOTALL)
_HREF_RE = re.compile(r'<link[^>]+href=["\']([^"\']+)["\']')
_MEDIA_RE = re.compile(r'<media:content[^>]+url=["\']([^"\']+)["\']')
_ENCLOSURE_RE = re.compile(r'<enclosure[^>]+url=["\']([^"\']+)["\'][^>]+type=["\']image/')
_IMG_RE = re.compile(r'<img[^>]+src=["\']([^"\']+)["\']')
_OG_IMAGE_RE = re.compile(
    r'<meta[^>]+(?:property|name)=["\']og:image(?::secure_url)?["\'][^>]+content=["\']([^"\']+)["\']'
    r'|<meta[^>]+content=["\']([^"\']+)["\'][^>]+(?:property|name)=["\']og:image(?::secure_url)?["\']'
)
# 파일명이 스크린샷임을 자인하는 이미지 — 대표 이미지로 부적합 (UI 캡처 노출 사고)
_SCREENSHOT_NAME_RE = re.compile(r"screen[-_]?shot", re.IGNORECASE)
# re.ASCII — feeds.ts::slugify 의 `\w` 는 `u` 플래그가 없는 JS 정규식이라 ASCII
# word-char 만 매치한다(비ASCII 는 전부 제거됨). 그대로 맞춘다.
_NON_WORD_RE = re.compile(r"[^\w\s-]", re.ASCII)
_MULTI_DASH_RE = re.compile(r"-+")
_TRIM_DASH_RE = re.compile(r"^-+|-+$")


def is_http_url(url: str | None) -> str | None:
    if not url:
        return None
    try:
        parsed = urllib.parse.urlparse(url)
    except ValueError:
        return None
    return url if parsed.scheme in ("http", "https") else None


def detect_region(text: str, fallback: str | None = None) -> str | None:
    lower = text.lower()
    for region, keywords in REGION_KEYWORDS.items():
        if any(kw in lower for kw in keywords):
            return region
    return fallback


def detect_category(text: str) -> str:
    lower = text.lower()
    for category, keywords in CATEGORY_KEYWORDS.items():
        if any(kw in lower for kw in keywords):
            return category
    return "environment"


def detect_country(text: str) -> str | None:
    lower = text.lower()
    for code, keywords in COUNTRY_KEYWORDS.items():
        if any(kw in lower for kw in keywords):
            return code
    return None


def heuristic_top_story(title: str, summary: str | None) -> bool:
    text = f"{title} {summary or ''}".lower()
    return any(kw in text for kw in STRONG_TOP_STORY_SIGNALS)


def slugify(title: str, max_len: int = 80) -> str:
    base = title.lower()
    base = _NON_WORD_RE.sub("", base)
    base = _WS_RE.sub("-", base)
    base = _MULTI_DASH_RE.sub("-", base)
    base = _TRIM_DASH_RE.sub("", base)
    base = base[:max_len]
    base = base.rstrip("-")
    return base or f"article-{int(time.time() * 1000):x}"


def extract_tag(xml: str, tag: str) -> str | None:
    start_idx = -1
    for open_variant in (f"<{tag}>", f"<{tag} "):
        start_idx = xml.find(open_variant)
        if start_idx != -1:
            break
    if start_idx == -1:
        return None
    content_start = xml.find(">", start_idx) + 1
    close_tag = f"</{tag}>"
    end_idx = xml.find(close_tag, content_start)
    if end_idx == -1:
        return None
    return xml[content_start:end_idx].strip()


def clean_content(raw: str | None) -> str | None:
    if not raw:
        return None
    text = raw
    m = _CDATA_RE.search(text)
    if m:
        text = m.group(1)
    # 엔티티 2단 복호: RSS description 은 흔히 "XML-escape 된 HTML"
    # (&lt;p&gt;El Ni&amp;#xF1;o&lt;/p&gt;) — 1차 unescape 로 마크업을 되살려
    # 태그를 벗기고, 2차 unescape 로 본문 자체의 엔티티(&#xF1; 등)를 푼다.
    # 평문 입력엔 두 번 다 no-op. 미복호 방치가 "El Ni&#xF1;o" 카드 제목 사고.
    text = html.unescape(text)
    text = _TAG_RE.sub("", text).strip()
    text = html.unescape(text)
    text = _WS_RE.sub(" ", text)
    if len(text) > 500:
        text = text[:497] + "..."
    return text or None


def extract_image(item_xml: str) -> tuple[str | None, bool]:
    """(image_url, weak). media:content/enclosure = 발행사가 지정한 대표 이미지(강한
    출처). 본문 첫 <img> 는 임베드일 수 있는 약한 폴백 — weak=True 로 표시해
    og:image 승격 대상으로 넘긴다."""
    # XML/HTML 속성값의 &amp; 는 복호해야 실제 URL 이다 — 방치하면 쿼리스트링이
    # `width=140&amp;quality=85` 로 깨져 이미지 리사이저 파라미터가 무효가 된다.
    m = _MEDIA_RE.search(item_xml)
    if m:
        return _unescape(m.group(1)), False
    m = _ENCLOSURE_RE.search(item_xml)
    if m:
        return _unescape(m.group(1)), False
    m = _IMG_RE.search(item_xml)
    if m:
        return _unescape(m.group(1)), True
    return None, False


def parse_og_image(html: str) -> str | None:
    """HTML 에서 og:image content 를 추출한다 (property/content 순서 양방향)."""
    m = _OG_IMAGE_RE.search(html)
    if not m:
        return None
    return is_http_url(_unescape(m.group(1) or m.group(2)))


def fetch_og_image(article_url: str, timeout: int = OG_FETCH_TIMEOUT_S) -> str | None:
    """기사 페이지의 og:image. 실패는 None — 수집을 죽이지 않는다."""
    req = urllib.request.Request(article_url, headers={"User-Agent": "AirLens-NewsCollector/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 — 수집된 기사 URL(http/https 검증 완료)만
            html = resp.read(OG_HTML_READ_CAP).decode("utf-8", "replace")
    except (urllib.error.URLError, OSError, ValueError):
        return None
    return parse_og_image(html)


def upgrade_weak_images(
    articles: list[dict[str, object]],
    fetch: Callable[[str], str | None] = fetch_og_image,
    max_fetches: int = MAX_OG_FETCHES_PER_RUN,
) -> None:
    """약한 출처(본문 첫 <img>) 이미지를 og:image 로 승격 (in-place, best-effort).

    - og:image 성공 → 교체 (스크린샷 파일명이 아니면).
    - og:image 실패/스크린샷 → **약한 이미지는 버린다** (None — 프론트 gradient
      placeholder 폴백). 본문 첫 <img> 는 쿠키 배너·뉴스레터 팝업·사이트 UI 캡처일
      수 있고, 그게 카드 대표 이미지로 노출된 실사고가 있다(2026-09-02 UX 평가).
      발행사가 대표로 지정하지 않았고 og:image 로도 확인 못 한 이미지를 싣는 것보다
      정직한 placeholder 가 낫다.
    - 강한 출처(media/enclosure) 이미지는 건드리지 않는다.
    """
    fetches = 0
    for art in articles:
        weak = art.pop("_image_weak", False)
        img = art.get("image_url")
        if not weak or not isinstance(img, str):
            continue
        og: str | None = None
        if fetches < max_fetches:
            fetches += 1
            og = fetch(str(art["article_url"]))
        if og and not _SCREENSHOT_NAME_RE.search(og):
            art["image_url"] = og
        else:
            art["image_url"] = None


def parse_date(date_str: str) -> datetime | None:
    """RFC822(pubDate) 우선, 실패하면 ISO8601/RFC3339(atom) 시도. 둘 다 실패하면 None."""
    try:
        dt = email.utils.parsedate_to_datetime(date_str)
        if dt is not None:
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        pass
    try:
        dt = datetime.fromisoformat(date_str.strip().replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def parse_rss_feed(xml: str) -> list[dict[str, object]]:
    """rss-parser.ts::parseRssFeed 이식. 필수 필드(title/url/유효 날짜) 없으면 skip."""
    articles: list[dict[str, object]] = []
    items = [m.group(0) for m in _ITEM_RE.finditer(xml)]

    for item in items[:MAX_ARTICLES_PER_FEED]:
        title = clean_content(extract_tag(item, "title"))
        if not title:
            continue

        url = clean_content(extract_tag(item, "link"))
        if not url:
            href_match = _HREF_RE.search(item)
            if href_match:
                url = _unescape(href_match.group(1))
        if not url:
            continue

        date_str = (
            extract_tag(item, "pubDate")
            or extract_tag(item, "published")
            or extract_tag(item, "dc:date")
            or extract_tag(item, "updated")
        )
        # feeds.ts 와 동일: 날짜 태그 자체가 없으면 지금 시각으로, 있는데 파싱이
        # 실패하면(형식 불명) 그 항목은 버린다.
        pub_dt = parse_date(date_str) if date_str else datetime.now(timezone.utc)
        if pub_dt is None:
            continue

        summary = clean_content(
            extract_tag(item, "description") or extract_tag(item, "summary") or extract_tag(item, "content")
        )

        valid_url = is_http_url(url)
        if not valid_url:
            continue

        image_raw, image_weak = extract_image(item)
        image_url = is_http_url(image_raw)
        articles.append({
            "title": title,
            "summary": summary,
            "article_url": valid_url,
            "published_at": pub_dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
            "image_url": image_url,
            "_image_weak": image_weak and image_url is not None,
        })

    return articles


def normalize_article(parsed: dict[str, object], feed: dict[str, object]) -> dict[str, object]:
    """enrichment.ts::enrichArticle + index.ts 의 row 조립을 합친 이식.

    summary_ko / sentiment_score / related_policy_id 는 원본도 채우지 않는
    필드다(LLM 레이어는 이번 범위 밖) — null 로 둔다.
    """
    title = str(parsed["title"])
    summary = parsed.get("summary")
    text = f"{title} {summary or ''}"
    raw_summary = str(summary) if summary else ""

    return {
        "title": title,
        "summary": summary,
        "source_name": feed["name"],
        "source_url": feed["site_url"],
        "article_url": parsed["article_url"],
        "published_at": parsed["published_at"],
        "region": detect_region(text, feed.get("default_region")),
        "country_code": detect_country(text),
        "topic": feed["default_topic"],
        "image_url": parsed.get("image_url"),
        "_image_weak": bool(parsed.get("_image_weak")),  # upgrade_weak_images 가 pop
        "category": detect_category(text),
        "summary_en": raw_summary[:200] or None,
        "summary_ko": None,
        "is_top_story": heuristic_top_story(title, summary if isinstance(summary, str) else None),
        "slug": slugify(title),
    }


def fetch_feed_xml(url: str, timeout: int = FETCH_TIMEOUT_S) -> tuple[str | None, str | None]:
    """(xml, None) 성공 또는 (None, 에러 메시지). 피드 하나의 실패가 전체를 죽이지 않는다."""
    req = urllib.request.Request(url, headers={"User-Agent": "AirLens-NewsCollector/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 — 고정 RSS 소스 목록만
            return resp.read().decode("utf-8", "replace"), None
    except urllib.error.HTTPError as e:
        return None, f"HTTP {e.code}"
    except (urllib.error.URLError, OSError) as e:
        return None, str(e)


def collect_all_feeds(feeds: list[dict[str, object]] = FEEDS) -> tuple[list[dict[str, object]], dict[str, int]]:
    """모든 피드를 최선노력(best-effort)으로 수집. (전체 기사 리스트, 소스별 건수)."""
    all_articles: list[dict[str, object]] = []
    per_source: dict[str, int] = {}

    for feed in feeds:
        name = str(feed["name"])
        xml, err = fetch_feed_xml(str(feed["url"]))
        if xml is None:
            print(f"  {name}: 전송 실패 ({err})", file=sys.stderr)
            per_source[name] = 0
            continue
        try:
            parsed = parse_rss_feed(xml)
        except Exception as e:  # noqa: BLE001 — 피드 하나의 파서 결함이 전체 수집을 죽이지 않게
            print(f"  {name}: 파싱 실패 ({e})", file=sys.stderr)
            per_source[name] = 0
            continue
        normalized = [normalize_article(p, feed) for p in parsed]
        per_source[name] = len(normalized)
        all_articles.extend(normalized)
        print(f"  {name}: {len(normalized)}건")

    return all_articles, per_source


def load_existing(path: str | None) -> list[dict[str, object]]:
    """직전 발행본(HF 에서 미리 내려받은 로컬 사본)을 읽는다. 없거나 깨졌으면 빈 베이스라인."""
    if not path:
        return []
    p = pathlib.Path(path)
    if not p.is_file() or p.stat().st_size == 0:
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        print(f"기존 발행 파일을 읽지 못했다 ({e}) — 빈 베이스라인으로 진행", file=sys.stderr)
        return []
    articles = data.get("articles") if isinstance(data, dict) else None
    if not isinstance(articles, list):
        print("기존 발행 파일 스키마가 예상과 다르다(articles 배열 없음) — 빈 베이스라인으로 진행", file=sys.stderr)
        return []
    kept = [a for a in articles if isinstance(a, dict) and a.get("article_url")]
    for a in kept:
        sanitize_stored_article(a)
    return kept


def sanitize_stored_article(art: dict[str, object]) -> None:
    """unescape 도입(2026-09-05) 이전에 발행된 레코드의 잔존 엔티티를 로드 시점에
    푼다 (in-place, 멱등). 179/400 건이 "El Ni&#xF1;o" 류 제목·`&amp;` 깨진 이미지
    URL 을 이미 실었다 — 파이프라인만 고치면 피드 창을 벗어난 옛 기사는 영원히
    더러운 채 남는다. slug 는 절대 건드리지 않는다(발행된 링크 안정성 — merge 규약).
    """
    for key in ("title", "summary", "summary_en", "summary_ko"):
        v = art.get(key)
        if isinstance(v, str):
            art[key] = _unescape(_unescape(v))
    img = art.get("image_url")
    if isinstance(img, str):
        art["image_url"] = _unescape(img)


def merge_articles(
    existing: list[dict[str, object]],
    fetched: list[dict[str, object]],
    max_articles: int = MAX_ARTICLES,
) -> list[dict[str, object]]:
    """article_url 기준 병합 + slug 고유성 보정 + published_at 내림차순 상위 N.

    slug 우선순위: 기존에 이미 발행된 slug 는 절대 바뀌지 않는다(외부 링크 안정성).
    새로 추가되는 기사끼리 slug 가 충돌하면(동일/유사 제목) article_url 해시 6자를
    덧붙여 고유화한다.
    """
    by_url: dict[str, dict[str, object]] = {}
    order: list[str] = []  # slug 우선순위 = 이 순서(기존 → 신규 발견 순)

    for art in existing:
        url = art.get("article_url")
        if not isinstance(url, str) or url in by_url:
            continue
        by_url[url] = dict(art)
        order.append(url)

    for art in fetched:
        url = str(art["article_url"])
        if url in by_url:
            preserved_slug = by_url[url].get("slug")
            updated = dict(art)
            if preserved_slug:
                updated["slug"] = preserved_slug
            by_url[url] = updated
        else:
            by_url[url] = dict(art)
            order.append(url)

    used_slugs: set[str] = set()
    for url in order:
        rec = by_url[url]
        base_slug = str(rec.get("slug") or slugify(str(rec["title"])))
        slug = base_slug
        if slug in used_slugs:
            suffix = hashlib.sha256(url.encode("utf-8")).hexdigest()[:6]
            slug = f"{base_slug}-{suffix}"
        used_slugs.add(slug)
        rec["slug"] = slug

    merged = [by_url[u] for u in order]
    merged.sort(key=lambda r: str(r.get("published_at") or ""), reverse=True)
    return merged[:max_articles]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="AirLens 뉴스 RSS 수집 (Supabase-free)")
    ap.add_argument("--out", default="articles.json", help="병합된 출력 JSON 경로")
    ap.add_argument("--existing", default=None, help="직전 발행본 로컬 사본 경로(없으면 빈 베이스라인)")
    args = ap.parse_args(argv)

    # 명시적으로 모듈 전역 FEEDS 를 넘긴다 — collect_all_feeds 의 기본 인자는
    # 정의 시점에 바인딩되므로, 여기서 인자 없이 호출하면 테스트의
    # monkeypatch.setattr(cn, "FEEDS", ...) 가 반영되지 않는다.
    fetched, per_source = collect_all_feeds(FEEDS)
    total_fetched = len(fetched)
    print(f"수집 합계: {total_fetched}건 (피드 {len(FEEDS)}개)")
    upgrade_weak_images(fetched)

    if total_fetched == 0:
        msg = (
            "모든 RSS 피드가 0건을 반환했다(전송 실패 또는 빈 응답) — 이전 발행본을 "
            "덮지 않기 위해 업로드 없이 중단한다."
        )
        print(msg, file=sys.stderr)
        print(f"::error title=News empty collect::{msg}")
        raise SystemExit(1)

    existing = load_existing(args.existing)
    merged = merge_articles(existing, fetched, MAX_ARTICLES)

    payload = {
        "refTime": datetime.now(timezone.utc).isoformat(),
        "count": len(merged),
        "articles": merged,
    }
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))

    size = os.path.getsize(args.out)
    print(
        f"Done: 신규 {total_fetched}건 수집 + 기존 {len(existing)}건 병합 → "
        f"{len(merged)}건 발행 → {args.out} ({size:,} bytes)"
    )
    print(f"소스별 분포: {json.dumps(per_source, ensure_ascii=False)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
