"""네이버 뉴스에서 특정 키워드 관련 새 기사를 찾아 텔레그램으로 알려주는 스크립트.

네이버 오픈API 키 없이, 네이버 뉴스 검색 결과 페이지를 직접 읽어와 사용한다
(검색 오픈API 신규 발급이 막혀 있어 API 키 없이도 동작하도록 구성).

GitHub Actions 스케줄(하루 3회)로 실행되는 것을 전제로 한다.
- 이미 알림을 보낸 기사는 seen.json 에 기록해 다시 보내지 않는다.
- 새 기사가 없으면 아무 메시지도 보내지 않는다.
- 처음 실행될 때는 기존 기사를 기준선으로만 저장하고 알림은 보내지 않는다
  (봇을 켜기 전부터 있던 기사까지 한꺼번에 쏟아지는 것을 막기 위함).
"""

import html
import json
import os
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
    return f"📰 <b>{title}</b>{press_part}\n{item['link']}"


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
