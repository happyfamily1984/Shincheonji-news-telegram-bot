"""네이버 뉴스에서 특정 키워드 관련 새 기사를 찾아 텔레그램으로 알려주는 스크립트.

네이버 오픈API 키 없이, 네이버 뉴스 검색 결과 페이지를 직접 읽어와 사용한다
(검색 오픈API 신규 발급이 막혀 있어 API 키 없이도 동작하도록 구성).

GitHub Actions 스케줄(하루 3회)로 실행되는 것을 전제로 한다.
- 이미 알림을 보낸 기사는 seen.json 에 기록해 다시 보내지 않는다.
- 새 기사가 없으면 아무 메시지도 보내지 않는다.
- 처음 실행될 때는 기존 기사를 기준선으로만 저장하고 알림은 보내지 않는다
  (봇을 켜기 전부터 있던 기사까지 한꺼번에 쏟아지는 것을 막기 위함).
- 기사 본문에서 키워드가 포함된 문장과 그 앞뒤 EXCERPT_CONTEXT_SENTENCES개
  문장을 발췌해 함께 보여준다 (외부 AI 요약 API 없이 동작).
"""

import html
import json
import os
import re
import sys
from pathlib import Path

import requests
from bs4 import BeautifulSoup

QUERY = os.environ.get("NEWS_QUERY") or "신천지"
TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]

SEEN_FILE = Path(__file__).parent / "seen.json"
MAX_SEEN = 1000
TELEGRAM_MAX_LEN = 3500
SEARCH_PAGES = 3  # 페이지당 최대 10건, 최근 30건까지 확인
ARTICLE_TEXT_MAX_LEN = 6000  # 발췌 대상으로 가져올 기사 본문 최대 길이
EXCERPT_MAX_LEN = 500  # 텔레그램 메시지에 넣을 발췌 최대 길이
EXCERPT_CONTEXT_SENTENCES = 1  # 키워드 문장 앞뒤로 가져올 문장 수

NEWS_URL = "https://search.naver.com/search.naver"
REQUEST_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    )
}


NEW_WINDOW_LABEL = "새 창 열림"  # 네이버가 target="_blank" 링크마다 넣는 스크린리더용 숨김 문구


def clean_link_text(tag):
    """앵커 텍스트에서 스크린리더용 숨김 문구('새 창 열림')를 제거한다."""
    text = tag.get_text(strip=True)
    while text.endswith(NEW_WINDOW_LABEL):
        text = text[: -len(NEW_WINDOW_LABEL)].strip()
    return text


def find_press(title_anchor, max_levels=8):
    """제목 링크에서 위로 올라가며 같은 카드 안의 언론사 링크를 찾는다.

    네이버 검색 결과 카드의 클래스명은 빌드마다 무작위 해시라 CSS 클래스로
    선택할 수 없다. 대신 data-heatmap-target 속성(.tit=제목, .prof=언론사)은
    안정적으로 유지되므로 이를 기준으로 같은 카드 내에서 언론사 링크를 찾는다.
    같은 카드 안에 언론사 링크가 두 개(썸네일 이미지용, 언론사 이름 텍스트용) 있는데
    썸네일용은 숨김 문구만 있어 clean_link_text 적용 후 비어 있으므로 자연히 건너뛴다.
    """
    node = title_anchor
    for _ in range(max_levels):
        node = node.parent
        if node is None:
            break
        for prof in node.select('a[data-heatmap-target=".prof"]'):
            text = clean_link_text(prof)
            if text:
                return text
    return ""


def fetch_news():
    items = []
    seen_links = set()

    for page in range(SEARCH_PAGES):
        start = page * 10 + 1
        params = {"where": "news", "query": QUERY, "sort": "1", "start": start}
        resp = requests.get(NEWS_URL, headers=REQUEST_HEADERS, params=params, timeout=15)
        resp.raise_for_status()

        soup = BeautifulSoup(resp.text, "html.parser")
        title_anchors = soup.select('a[data-heatmap-target=".tit"]')
        if not title_anchors:
            break

        for a in title_anchors:
            link = a.get("href")
            title = clean_link_text(a)
            if not link or link in seen_links:
                continue
            seen_links.add(link)
            items.append({"title": title, "link": link, "press": find_press(a)})

    if not items:
        print(
            "[warn] 0 articles parsed from search results — "
            "Naver's page markup may have changed and selectors may need an update.",
            file=sys.stderr,
        )

    return items


def fetch_article_text(url):
    """기사 페이지에서 본문으로 추정되는 텍스트를 추출한다(요약용, 최선 노력).

    resp.content(원본 바이트)를 넘겨 BeautifulSoup이 메타 태그 등으로 인코딩을
    직접 판별하게 한다. Content-Type에 charset이 없는 구형 언론사 사이트(EUC-KR 등)를
    resp.text(= requests가 추정한 인코딩)로 읽으면 글자가 깨질 수 있기 때문.
    """
    resp = requests.get(url, headers=REQUEST_HEADERS, timeout=15)
    resp.raise_for_status()

    soup = BeautifulSoup(resp.content, "html.parser")
    for tag in soup(["script", "style", "nav", "header", "footer", "aside"]):
        tag.decompose()

    paragraphs = [p for p in soup.find_all("p") if p.get_text(strip=True)]
    if paragraphs:
        # 기사 본문 <p>들은 보통 같은 컨테이너 안에 몰려 있고, 관련기사/광고 위젯의
        # <p>는 페이지 곳곳에 흩어져 있다. 같은 부모를 공유하는 <p>들을 묶어 텍스트
        # 분량이 가장 많은 그룹을 본문으로 간주해, 무관한 위젯 텍스트가 섞이는 것을 줄인다.
        groups = {}
        for p in paragraphs:
            groups.setdefault(id(p.parent), []).append(p)
        best_group = max(groups.values(), key=lambda ps: sum(len(p.get_text(strip=True)) for p in ps))
        text = " ".join(p.get_text(" ", strip=True) for p in best_group)
        return text[:ARTICLE_TEXT_MAX_LEN]

    # <p> 태그 기반 본문을 못 찾은 경우(기사 본문을 자바스크립트로 렌더링하는
    # 사이트 등). 이때 페이지 전체 텍스트를 긁으면 내비게이션 메뉴/카테고리
    # 목록이 섞여 들어가므로, 그 대신 언론사가 제공하는 요약 메타 태그
    # (og:description)를 쓰는 편이 안전하다. 그것도 없으면 발췌 없이 넘어간다.
    meta = soup.find("meta", attrs={"property": "og:description"}) or soup.find(
        "meta", attrs={"name": "description"}
    )
    content = meta.get("content", "").strip() if meta else ""
    return content[:ARTICLE_TEXT_MAX_LEN]


SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")


def split_sentences(text):
    text = " ".join(text.split())  # 공백/줄바꿈 정규화
    if not text:
        return []
    return [s.strip() for s in SENTENCE_SPLIT_RE.split(text) if s.strip()]


def extract_context(article_text, keyword):
    """키워드가 포함된 문장과 그 앞뒤 EXCERPT_CONTEXT_SENTENCES개 문장을 이어 붙인다.

    키워드가 여러 번 나오면 각 등장 지점 주변을 모두 모으되, 범위가 겹치면 하나로
    합치고, 겹치지 않는 범위끼리는 " (…) "로 구분해 이어 붙인다.
    """
    sentences = split_sentences(article_text)
    hit_indices = [i for i, s in enumerate(sentences) if keyword in s]
    if not hit_indices:
        return ""

    ranges = []
    for i in hit_indices:
        start = max(i - EXCERPT_CONTEXT_SENTENCES, 0)
        end = min(i + EXCERPT_CONTEXT_SENTENCES, len(sentences) - 1)
        if ranges and start <= ranges[-1][1] + 1:
            ranges[-1][1] = max(ranges[-1][1], end)
        else:
            ranges.append([start, end])

    excerpt = " (…) ".join(" ".join(sentences[s : e + 1]) for s, e in ranges)
    return excerpt[:EXCERPT_MAX_LEN]


def add_excerpt(item):
    try:
        article_text = fetch_article_text(item["link"])
        item["excerpt"] = extract_context(article_text, QUERY)
    except Exception as exc:  # noqa: BLE001
        print(f"[warn] failed to extract excerpt for '{item['title']}' ({item['link']}): {exc!r}", file=sys.stderr)
        item["excerpt"] = ""


def load_seen():
    if SEEN_FILE.exists():
        data = json.loads(SEEN_FILE.read_text(encoding="utf-8"))
        return data.get("seen_ids", []), data.get("initialized", False)
    return [], False


def save_seen(seen_ids, initialized):
    trimmed = seen_ids[-MAX_SEEN:]
    SEEN_FILE.write_text(
        json.dumps({"seen_ids": trimmed, "initialized": initialized}, ensure_ascii=False, indent=2)
        + "\n",
        encoding="utf-8",
    )


def send_telegram(text):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    resp = requests.post(
        url,
        data={
            "chat_id": TELEGRAM_CHAT_ID,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": "false",
        },
        timeout=15,
    )
    resp.raise_for_status()


def article_id(item):
    return item["link"]


def format_article(item):
    title = html.escape(item.get("title", ""))
    press = item.get("press", "")
    press_part = f" ({html.escape(press)})" if press else ""
    link = html.escape(item["link"], quote=True)

    lines = [f"📰 <b>{title}</b>{press_part}"]
    excerpt = item.get("excerpt", "")
    if excerpt:
        lines.append(f"📝 {html.escape(excerpt)}")
    lines.append(f'🔗 <a href="{link}">링크</a>')
    return "\n".join(lines)


def chunk_messages(articles):
    header = f"🔔 '{QUERY}' 관련 새 뉴스 {len(articles)}건\n\n"
    chunks = []
    current = header
    for art in articles:
        block = format_article(art) + "\n\n"
        if current != header and len(current) + len(block) > TELEGRAM_MAX_LEN:
            chunks.append(current)
            current = block
        else:
            current += block
    if current.strip():
        chunks.append(current)
    return chunks


def main():
    items = fetch_news()
    seen_ids, initialized = load_seen()
    seen_set = set(seen_ids)

    new_items = [it for it in items if article_id(it) not in seen_set]

    if not initialized:
        baseline_ids = seen_ids + [article_id(it) for it in new_items]
        save_seen(baseline_ids, True)
        print(f"[init] baseline saved with {len(items)} article(s); no notification sent.")
        return

    if not new_items:
        print("No new articles. Nothing sent.")
        return

    new_items.reverse()  # 오래된 기사부터 순서대로 발송

    for item in new_items:
        add_excerpt(item)

    for chunk in chunk_messages(new_items):
        send_telegram(chunk)

    updated_ids = seen_ids + [article_id(it) for it in new_items]
    save_seen(updated_ids, True)
    print(f"Sent {len(new_items)} new article(s).")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # noqa: BLE001
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)
