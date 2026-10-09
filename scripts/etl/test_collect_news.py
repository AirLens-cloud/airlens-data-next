"""collect_news.py 계약 시험.

collect_firms.py 규약(테스트 가능한 단일 정본)을 따른다 — 네트워크 호출 0
(urlopen 대체), 시크릿 없음(이 수집기는 인증이 필요 없는 공개 RSS 만 읽는다).

AAA(Arrange-Act-Assert) 패턴. 요구 커버리지(작업 지시): 정상 파싱 / 빈 응답 →
예외(덮어쓰기 금지) / 중복 URL 병합 / 필수 필드 누락 시 skip / slug 생성 규칙.
"""
from __future__ import annotations

import io
import json
import urllib.error
import urllib.request

import pytest

import collect_news as cn

RSS_HEADER = "<?xml version='1.0'?><rss><channel>"
RSS_FOOTER = "</channel></rss>"


def _rss_item(
    title: str = "Air quality worsens in Seoul",
    link: str = "https://example.com/article-1",
    pub_date: str = "Tue, 01 Jan 2026 00:00:00 GMT",
    description: str = "PM2.5 levels rose sharply this week.",
) -> str:
    parts = ["<item>"]
    if title is not None:
        parts.append(f"<title>{title}</title>")
    if link is not None:
        parts.append(f"<link>{link}</link>")
    if pub_date is not None:
        parts.append(f"<pubDate>{pub_date}</pubDate>")
    if description is not None:
        parts.append(f"<description>{description}</description>")
    parts.append("</item>")
    return "".join(parts)


def _feed_xml(*items: str) -> str:
    return RSS_HEADER + "".join(items) + RSS_FOOTER


def _feed(name: str = "Test Feed", url: str = "https://feed.example.com/rss") -> dict:
    return {"name": name, "url": url, "site_url": "https://example.com", "default_topic": "policy"}


def _responses(monkeypatch, script: dict[str, str | Exception]):
    """feed url → 응답(str) 또는 raise 할 예외. 매핑 없는 url 은 빈 XML."""
    calls: list[str] = []

    def fake_urlopen(req, timeout=None):
        url = req.full_url if hasattr(req, "full_url") else req
        calls.append(url)
        outcome = script.get(url, _feed_xml())
        if isinstance(outcome, Exception):
            raise outcome
        return io.BytesIO(outcome.encode())

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    return calls


# ---------------------------------------------------------------------------
# 정상 파싱
# ---------------------------------------------------------------------------

def test_happy_path_parses_and_normalizes_article():
    # Arrange
    xml = _feed_xml(_rss_item(
        title="Seoul enacts new clean air law",
        link="https://example.com/seoul-law",
        description="The government passed emergency regulation on PM2.5.",
    ))

    # Act
    articles = cn.parse_rss_feed(xml)

    # Assert
    assert len(articles) == 1
    a = articles[0]
    assert a["title"] == "Seoul enacts new clean air law"
    assert a["article_url"] == "https://example.com/seoul-law"
    assert a["published_at"].startswith("2026-01-01T00:00:00")
    assert "PM2.5" in a["summary"]


def test_main_writes_full_schema_payload(monkeypatch, tmp_path):
    # Arrange
    feed = _feed(name="UNEP", url="https://feed.example.com/rss")
    monkeypatch.setattr(cn, "FEEDS", [feed])
    xml = _feed_xml(_rss_item(
        title="Government bans coal plants",
        link="https://example.com/coal-ban",
        description="A court ruling forced the ministry to act.",
    ))
    _responses(monkeypatch, {feed["url"]: xml})
    out = tmp_path / "articles.json"

    # Act
    assert cn.main(["--out", str(out)]) == 0

    # Assert
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["count"] == 1
    art = data["articles"][0]
    assert art["source_name"] == "UNEP"
    assert art["article_url"] == "https://example.com/coal-ban"
    assert art["topic"] == "policy"
    assert art["category"] == "policy"  # "court ruling"/"ministry" 키워드 매치
    assert art["slug"] == "government-bans-coal-plants"
    # LLM 레이어 미착수 필드는 null (0/빈문자열 날조 금지)
    assert art["summary_ko"] is None
    assert "sentiment_score" not in art
    assert "related_policy_id" not in art


# ---------------------------------------------------------------------------
# 빈 응답 → 예외 (덮어쓰기 금지)
# ---------------------------------------------------------------------------

def test_all_feeds_empty_fails_loud_without_writing(monkeypatch, tmp_path, capsys):
    # Arrange
    feeds = [_feed(name="A", url="https://a.example.com/rss"), _feed(name="B", url="https://b.example.com/rss")]
    monkeypatch.setattr(cn, "FEEDS", feeds)
    _responses(monkeypatch, {f["url"]: _feed_xml() for f in feeds})  # 아이템 0개
    out = tmp_path / "articles.json"

    # Act / Assert
    with pytest.raises(SystemExit) as exc:
        cn.main(["--out", str(out)])
    assert exc.value.code == 1
    assert not out.exists()
    assert "News empty collect" in capsys.readouterr().out


def test_all_feeds_transport_failure_fails_loud(monkeypatch, tmp_path):
    # Arrange
    feed = _feed()
    monkeypatch.setattr(cn, "FEEDS", [feed])
    _responses(monkeypatch, {feed["url"]: urllib.error.URLError("network down")})
    out = tmp_path / "articles.json"

    # Act / Assert
    with pytest.raises(SystemExit) as exc:
        cn.main(["--out", str(out)])
    assert exc.value.code == 1
    assert not out.exists()


def test_one_feed_failing_does_not_block_others(monkeypatch):
    # Arrange
    good = _feed(name="Good", url="https://good.example.com/rss")
    bad = _feed(name="Bad", url="https://bad.example.com/rss")
    xml = _feed_xml(_rss_item(link="https://example.com/ok"))
    _responses(monkeypatch, {good["url"]: xml, bad["url"]: urllib.error.URLError("timeout")})

    # Act
    articles, per_source = cn.collect_all_feeds([good, bad])

    # Assert
    assert per_source == {"Good": 1, "Bad": 0}
    assert len(articles) == 1


# ---------------------------------------------------------------------------
# 중복 URL 병합
# ---------------------------------------------------------------------------

def test_merge_dedupes_by_article_url_and_updates_content():
    # Arrange
    existing = [{
        "title": "Old title", "summary": "old", "source_name": "UNEP",
        "source_url": "https://example.com", "article_url": "https://example.com/x",
        "published_at": "2026-01-01T00:00:00Z", "region": None, "country_code": None,
        "topic": "policy", "image_url": None, "category": "policy",
        "summary_en": None, "summary_ko": None, "is_top_story": False,
        "slug": "old-title",
    }]
    fresh = [{
        "title": "Updated title", "summary": "new", "source_name": "UNEP",
        "source_url": "https://example.com", "article_url": "https://example.com/x",
        "published_at": "2026-01-02T00:00:00Z", "region": "asia", "country_code": "KR",
        "topic": "policy", "image_url": None, "category": "environment",
        "summary_en": "new", "summary_ko": None, "is_top_story": True,
        "slug": "updated-title",
    }]

    # Act
    merged = cn.merge_articles(existing, fresh, max_articles=10)

    # Assert
    assert len(merged) == 1  # 같은 article_url — 중복 없이 병합
    assert merged[0]["title"] == "Updated title"  # 최신 내용으로 갱신
    assert merged[0]["slug"] == "old-title"  # 기존 slug 는 유지(외부 링크 안정성)


def test_merge_keeps_disjoint_articles_from_both_sides():
    # Arrange
    existing = [_article("Existing", "https://example.com/e", "2026-01-01T00:00:00Z")]
    fresh = [_article("Fresh", "https://example.com/f", "2026-01-02T00:00:00Z")]

    # Act
    merged = cn.merge_articles(existing, fresh, max_articles=10)

    # Assert
    urls = {a["article_url"] for a in merged}
    assert urls == {"https://example.com/e", "https://example.com/f"}
    assert merged[0]["article_url"] == "https://example.com/f"  # published_at desc


def test_merge_truncates_to_max_articles_keeping_newest():
    # Arrange
    existing = [
        _article("A", "https://example.com/a", "2026-01-01T00:00:00Z"),
        _article("B", "https://example.com/b", "2026-01-02T00:00:00Z"),
        _article("C", "https://example.com/c", "2026-01-03T00:00:00Z"),
    ]

    # Act
    merged = cn.merge_articles(existing, [], max_articles=2)

    # Assert
    assert [a["article_url"] for a in merged] == ["https://example.com/c", "https://example.com/b"]


# ---------------------------------------------------------------------------
# 필수 필드 누락 시 skip
# ---------------------------------------------------------------------------

def test_item_missing_title_is_skipped():
    xml = _feed_xml(_rss_item(title=None))
    assert cn.parse_rss_feed(xml) == []


def test_item_missing_link_is_skipped():
    xml = _feed_xml(_rss_item(link=None))
    assert cn.parse_rss_feed(xml) == []


def test_item_with_unparseable_date_is_skipped():
    xml = _feed_xml(_rss_item(pub_date="not-a-real-date"))
    assert cn.parse_rss_feed(xml) == []


def test_item_missing_date_tag_defaults_to_now():
    xml = _feed_xml(_rss_item(pub_date=None))
    articles = cn.parse_rss_feed(xml)
    assert len(articles) == 1  # 날짜 태그 자체가 없으면 skip 아니라 지금 시각


def test_item_with_non_http_link_is_skipped():
    xml = _feed_xml(_rss_item(link="javascript:alert(1)"))
    assert cn.parse_rss_feed(xml) == []


def test_mixed_valid_and_invalid_items_only_valid_survive():
    xml = _feed_xml(
        _rss_item(title=None, link="https://example.com/no-title"),
        _rss_item(title="Valid one", link="https://example.com/valid"),
    )
    articles = cn.parse_rss_feed(xml)
    assert len(articles) == 1
    assert articles[0]["article_url"] == "https://example.com/valid"


# ---------------------------------------------------------------------------
# slug 생성 규칙
# ---------------------------------------------------------------------------

def test_slugify_basic_rule():
    assert cn.slugify("Seoul Enacts New Clean Air Law") == "seoul-enacts-new-clean-air-law"


def test_slugify_strips_punctuation_and_collapses_dashes():
    assert cn.slugify("PM2.5: What's Next?! (2026 Report)") == "pm25-whats-next-2026-report"


def test_slugify_truncates_to_max_len_and_trims_trailing_dash():
    long_title = "word " * 40  # 매우 긴 제목
    slug = cn.slugify(long_title, max_len=20)
    assert len(slug) <= 20
    assert not slug.endswith("-")


def test_slugify_empty_title_falls_back_to_generated_id():
    assert cn.slugify("!!!").startswith("article-")


def test_merge_disambiguates_colliding_slugs_with_url_hash_suffix():
    # Arrange — 서로 다른 두 기사가 동일 제목(→ 동일 base slug)을 갖는 경우
    fresh = [
        _article("Same Headline", "https://example.com/one", "2026-01-01T00:00:00Z"),
        _article("Same Headline", "https://example.com/two", "2026-01-02T00:00:00Z"),
    ]

    # Act
    merged = cn.merge_articles([], fresh, max_articles=10)

    # Assert
    slugs = {a["slug"] for a in merged}
    assert len(slugs) == 2  # 충돌 없이 둘 다 고유
    assert any(s == "same-headline" for s in slugs)
    assert any(s.startswith("same-headline-") and s != "same-headline" for s in slugs)


def _article(title: str, url: str, published_at: str) -> dict:
    return {
        "title": title, "summary": None, "source_name": "Test",
        "source_url": "https://example.com", "article_url": url,
        "published_at": published_at, "region": None, "country_code": None,
        "topic": "policy", "image_url": None, "category": "environment",
        "summary_en": None, "summary_ko": None, "is_top_story": False,
        "slug": cn.slugify(title),
    }


# ---------------------------------------------------------------------------
# og:image 승격 (약한 첫 <img> 폴백 → 기사 페이지 og:image)
# ---------------------------------------------------------------------------

def test_extract_image_media_content_is_strong():
    item = '<item><media:content url="https://ex.com/a.jpg"/><img src="https://ex.com/b.jpg"></item>'
    url, weak = cn.extract_image(item)
    assert url == "https://ex.com/a.jpg"
    assert weak is False


def test_extract_image_first_img_fallback_is_weak():
    item = '<item><description><img src="https://ex.com/embed.png"></description></item>'
    url, weak = cn.extract_image(item)
    assert url == "https://ex.com/embed.png"
    assert weak is True


def test_parse_og_image_both_attribute_orders():
    assert cn.parse_og_image(
        '<meta property="og:image" content="https://ex.com/og.jpg"/>'
    ) == "https://ex.com/og.jpg"
    assert cn.parse_og_image(
        '<meta content="https://ex.com/og2.jpg" property="og:image"/>'
    ) == "https://ex.com/og2.jpg"
    assert cn.parse_og_image("<html><head></head></html>") is None


def _weak_article(img: str | None, url: str = "https://ex.com/article") -> dict:
    return {"article_url": url, "image_url": img, "_image_weak": img is not None}


def test_upgrade_replaces_weak_image_with_og_image():
    arts = [_weak_article("https://ex.com/embed.png")]
    cn.upgrade_weak_images(arts, fetch=lambda u: "https://ex.com/featured.jpg")
    assert arts[0]["image_url"] == "https://ex.com/featured.jpg"
    assert "_image_weak" not in arts[0]  # 내부 키는 발행물에 새지 않는다


def test_upgrade_nulls_screenshot_named_image_when_og_fails():
    arts = [_weak_article("https://ex.com/screenshot-123.png")]
    cn.upgrade_weak_images(arts, fetch=lambda u: None)
    assert arts[0]["image_url"] is None  # 프론트 placeholder 폴백


def test_upgrade_drops_weak_image_when_og_fails():
    # 정책 변경(2026-09-05): og 로 확인 못 한 약한 이미지는 파일명과 무관하게 버린다
    # — 본문 첫 <img> 가 쿠키 배너/사이트 UI 팝업이던 실사고(2026-09-02 UX 평가).
    arts = [_weak_article("https://ex.com/embed.jpg")]
    cn.upgrade_weak_images(arts, fetch=lambda u: None)
    assert arts[0]["image_url"] is None


def test_upgrade_rejects_screenshot_named_og_image():
    arts = [_weak_article("https://ex.com/screen_shot.png")]
    cn.upgrade_weak_images(arts, fetch=lambda u: "https://ex.com/screenshot-og.png")
    assert arts[0]["image_url"] is None  # og 도 스크린샷 — 이미지 제거


def test_upgrade_does_not_touch_strong_images():
    arts = [{"article_url": "https://ex.com/a", "image_url": "https://ex.com/strong.jpg",
             "_image_weak": False}]
    calls = []
    cn.upgrade_weak_images(arts, fetch=lambda u: calls.append(u))
    assert arts[0]["image_url"] == "https://ex.com/strong.jpg"
    assert calls == []  # 강한 출처엔 네트워크 0회
    assert "_image_weak" not in arts[0]


def test_upgrade_respects_fetch_cap():
    arts = [_weak_article(f"https://ex.com/e{i}.png", url=f"https://ex.com/a{i}") for i in range(5)]
    calls = []

    def fetch(u):
        calls.append(u)
        return "https://ex.com/og.jpg"

    cn.upgrade_weak_images(arts, fetch=fetch, max_fetches=3)
    assert len(calls) == 3  # 상한 초과분은 fetch 없이 통과
    assert arts[3]["image_url"] is None  # 상한 밖 — 미검증 약한 이미지는 버림(placeholder)


# ── 엔티티 복호 (2026-09-05 — "El Ni&#xF1;o" 카드 제목 사고) ──────────────────


def test_title_html_entities_are_decoded():
    xml = _feed_xml(_rss_item(title="El Ni&#xF1;o returns &amp; intensifies"))
    parsed = cn.parse_rss_feed(xml)
    assert parsed[0]["title"] == "El Niño returns & intensifies"


def test_description_xml_escaped_html_is_fully_decoded():
    # RSS description 의 흔한 형태: XML-escape 된 HTML. 1차 복호로 태그를 되살려
    # 벗기고, 2차 복호로 본문 엔티티를 푼다.
    xml = _feed_xml(_rss_item(description="&lt;p&gt;PM2.5 &amp;amp; ozone rose&lt;/p&gt;"))
    parsed = cn.parse_rss_feed(xml)
    assert parsed[0]["summary"] == "PM2.5 & ozone rose"


def test_extract_image_decodes_amp_in_query_string():
    item = '<item><media:content url="https://i.ex.com/a.jpg?width=140&amp;quality=85"/></item>'
    url, _weak = cn.extract_image(item)
    assert url == "https://i.ex.com/a.jpg?width=140&quality=85"


def test_parse_og_image_decodes_amp_in_query_string():
    tag = '<meta property="og:image" content="https://i.ex.com/og.jpg?w=1200&amp;fit=max"/>'
    assert cn.parse_og_image(tag) == "https://i.ex.com/og.jpg?w=1200&fit=max"


def test_sanitize_stored_article_decodes_text_and_image_but_not_slug():
    art = {
        "title": "El Ni&#xF1;o watch",
        "summary": "heat &amp; haze",
        "summary_en": "heat &amp; haze",
        "summary_ko": None,
        "image_url": "https://i.ex.com/a.jpg?w=140&amp;q=85",
        "slug": "el-nixf1o-watch",  # 발행된 slug 는 불변
        "article_url": "https://ex.com/nino",
    }
    cn.sanitize_stored_article(art)
    assert art["title"] == "El Niño watch"
    assert art["summary"] == "heat & haze"
    assert art["summary_en"] == "heat & haze"
    assert art["summary_ko"] is None
    assert art["image_url"] == "https://i.ex.com/a.jpg?w=140&q=85"
    assert art["slug"] == "el-nixf1o-watch"

    before = dict(art)
    cn.sanitize_stored_article(art)  # 멱등 — 재실행이 값을 더 바꾸지 않는다
    assert art == before
