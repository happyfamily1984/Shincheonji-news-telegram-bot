"""네이버 뉴스에서 특정 키워드 관련 새 기사를 찾아 텔레그램으로 알려주는 스크립트.

GitHub Actions 스케줄(하루 3회)로 실행되는 것을 전제로 한다.
- 이미 알림을 보낸 기사는 seen.json 에 기록해 다시 보내지 않는다.
- 새 기사가 없으면 아무 메시지도 보내지 않는다.
- 처음 실행될 때는 기존 기사를 기준선으로만 저장하고 알림은 보내지 않는다
  (봇을 켜기 전부터 있던 기사까지 한꺼번에 쏟아지는 것을 막기 위함).
"""

import html
import json
import os
import re
import sys
from pathlib import Path

import requests

QUERY = os.environ.get("NEWS_QUERY") or "신천지"
NAVER_CLIENT_ID = os.environ["NAVER_CLIENT_ID"]
NAVER_CLIENT_SECRET = os.environ["NAVER_CLIENT_SECRET"]
TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]

SEEN_FILE = Path(__file__).parent / "seen.json"
MAX_SEEN = 1000
TELEGRAM_MAX_LEN = 3500

TAG_RE = re.compile(r"<[^>]+>")


def clean_text(text: str) -> str:
    return html.unescape(TAG_RE.sub("", text)).strip()


def fetch_news():
    url = "https://openapi.naver.com/v1/search/news.json"
    headers = {
        "X-Naver-Client-Id": NAVER_CLIENT_ID,
        "X-Naver-Client-Secret": NAVER_CLIENT_SECRET,
    }
    params = {"query": QUERY, "display": 100, "sort": "date"}
    resp = requests.get(url, headers=headers, params=params, timeout=15)
    resp.raise_for_status()
    return resp.json().get("items", [])


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
    return item.get("link") or item.get("originallink")


def format_article(item):
    title = clean_text(item.get("title", ""))
    link = item.get("originallink") or item.get("link")
    pub_date = item.get("pubDate", "")
    return f"📰 <b>{html.escape(title)}</b>\n{link}\n{pub_date}"


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
