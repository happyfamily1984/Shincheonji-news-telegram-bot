# 신천지 뉴스 텔레그램 알림 봇

네이버 뉴스에서 **"신천지"** 관련 새 기사가 올라오면 텔레그램으로 알려주는 봇입니다.
서버 없이 **GitHub Actions**로 동작하며, 한국시간 오전 7시부터 오후 10시까지 매 정각
(1시간 간격, 하루 16회) 자동 실행됩니다.

## 동작 방식

1. 네이버 뉴스 검색 결과 페이지(`search.naver.com`)에서 "신천지" 키워드 최신 기사를
   최대 30건 가져옵니다 (별도 API 키 불필요).
2. 저장소에 커밋되어 있는 `seen.json`에 기록된, 이미 알림을 보낸 기사와 비교합니다.
3. **새 기사만** 전송 대상이며, 각 기사 본문을 가져와 **Gemini API로 2~3문장 요약**을
   생성한 뒤(`GEMINI_API_KEY` 미설정 시 요약 없이 진행) 텔레그램으로 전송하고,
   `seen.json`을 갱신해 다시 커밋합니다. 메시지의 기사 링크는 전체 URL 대신
   **"링크"라는 글자를 누르면 실제 기사로 연결**되는 형태로 표시됩니다.
4. 새 기사가 없으면 아무 메시지도 보내지 않습니다.
5. 봇을 처음 실행할 때는 그 시점의 기존 기사들을 기준선으로만 저장하고, 알림은 보내지
   않습니다(과거 기사가 한꺼번에 쏟아지는 것을 방지).

> 네이버 뉴스 검색 페이지의 HTML 구조가 나중에 또 바뀌면 파싱이 깨질 수 있습니다.
> (실제로 2026년 9월 UI 개편으로 클래스명이 빌드마다 무작위 해시로 바뀌어, 지금은
> 비교적 안정적인 `data-heatmap-target=".tit"`(제목), `".prof"`(언론사) 속성을
> 기준으로 파싱하도록 되어 있습니다.) Actions 로그에 `[warn] 0 articles parsed...`
> 메시지가 보이면 `news_bot.py`의 `fetch_news()` / `find_press()` 선택자를
> 다시 손봐야 한다는 신호입니다.

## 준비물

### 텔레그램 봇 생성

1. 텔레그램에서 [@BotFather](https://t.me/BotFather)와 대화 시작
2. `/newbot` 입력 후 안내에 따라 봇 이름 설정
3. 발급되는 **토큰**(예: `123456789:AAExxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx`)을 기록해둡니다.
4. 알림을 받을 본인 계정으로 그 봇과 대화를 한 번 시작합니다(아무 메시지나 전송, 예: `/start`).
5. **Chat ID 확인**: 브라우저에서 아래 주소를 열어 `"chat":{"id":...}` 값을 확인합니다.
   ```
   https://api.telegram.org/bot<발급받은 토큰>/getUpdates
   ```
   (그룹방으로 받고 싶다면 봇을 그룹에 초대한 뒤 그룹에서 메시지를 보내고 동일하게 확인)

### Gemini API 키 발급 (기사 요약용)

1. [Google AI Studio](https://aistudio.google.com/apikey)에서 Gemini API 키를 발급받습니다.
2. 발급받은 키를 아래 GitHub Secrets 등록 단계에서 `GEMINI_API_KEY`로 등록합니다.
3. `GEMINI_API_KEY`를 등록하지 않으면 요약 없이 제목/언론사/링크만 전송됩니다
   (기능이 없어도 봇은 정상 동작합니다).

## GitHub Secrets 등록

이 저장소의 **Settings → Secrets and variables → Actions → New repository secret**에서
아래 항목들을 등록합니다.

| 이름 | 값 |
|---|---|
| `TELEGRAM_BOT_TOKEN` | 텔레그램 봇 토큰 |
| `TELEGRAM_CHAT_ID` | 알림 받을 chat id |
| `GEMINI_API_KEY` | Gemini API 키 (선택, 기사 요약 기능에 사용) |

검색어를 "신천지"가 아닌 다른 키워드로 바꾸고 싶다면, 같은 화면의 **Variables** 탭에서
`NEWS_QUERY` 변수를 추가하면 됩니다 (없으면 기본값 "신천지" 사용).
요약에 사용할 Gemini 모델을 바꾸고 싶다면 같은 곳에 `GEMINI_MODEL` 변수를 추가하면
됩니다 (없으면 기본값 `gemini-flash-latest` 사용 — 항상 최신 Flash 모델을 가리키는
Google 공식 별칭이라 특정 모델이 단종되어도 자동으로 전환됩니다).

## 실행 확인

Secrets 등록 후 저장소의 **Actions** 탭 → `Naver News Notifier` 워크플로 →
**Run workflow** 버튼으로 수동 실행해 정상 동작하는지 확인할 수 있습니다.
첫 실행은 "기준선 저장"만 하고 알림을 보내지 않으니, 실제 알림이 오는지 확인하려면
두 번째 수동 실행부터 확인하시면 됩니다(그 사이 새 기사가 올라왔어야 합니다).

이후에는 매일 한국시간 07:00~22:00 사이 매 정각에 자동으로 실행됩니다
(`.github/workflows/notify.yml`의 cron 스케줄, GitHub Actions는 UTC 기준이라
UTC 22,23시(전날) + 00~13시로 등록되어 있습니다).

## 로컬 테스트 (선택)

```bash
pip install -r requirements.txt
export TELEGRAM_BOT_TOKEN=...
export TELEGRAM_CHAT_ID=...
python news_bot.py
```

## 파일 구성

```
news_bot.py                 # 메인 스크립트
requirements.txt            # 의존성 (requests, beautifulsoup4)
seen.json                   # 이미 알림 보낸 기사 기록 (워크플로가 자동으로 갱신/커밋)
.github/workflows/notify.yml  # 하루 16회(오전 7시~오후 10시, 매 정각) 스케줄 실행
```
