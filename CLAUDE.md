# YouTube Summarizer Skill

YouTube 영상을 요약해서 Notion에 자동 업로드하는 Skill입니다.

## 트리거 방법

사용자가 `/yt-summary <URL>` 또는 "이 영상 요약해줘 <URL>" 형태로 요청하면 아래 절차를 따릅니다.
SRT 자막 파일이 첨부된 경우 함께 처리합니다.

---

## 절차

### Case 1. 자막 파일 없음

GitHub Actions workflow를 바로 트리거합니다.

```
POST https://api.github.com/repos/cultivo-hy/summary-images/actions/workflows/summarize.yml/dispatches
Authorization: token {GITHUB_TOKEN}
Content-Type: application/json

{
  "ref": "main",
  "inputs": {
    "youtube_url": "<사용자가 준 URL>",
    "model": "gemini-2.5-flash-lite"
  }
}
```

### Case 2. SRT 자막 파일 첨부된 경우

**Step 1. SRT 파일을 GitHub repo에 업로드**

첨부된 SRT 내용을 base64로 인코딩하여 GitHub API로 업로드합니다.

```
PUT https://api.github.com/repos/cultivo-hy/summary-images/contents/transcripts/{video_id}.srt
Authorization: token {GITHUB_TOKEN}
Content-Type: application/json

{
  "message": "Add transcript for {video_id}",
  "content": "<base64 인코딩된 SRT 내용>"
}
```

**Step 2. transcript_path를 포함하여 workflow 트리거**

```
POST https://api.github.com/repos/cultivo-hy/summary-images/actions/workflows/summarize.yml/dispatches
Authorization: token {GITHUB_TOKEN}
Content-Type: application/json

{
  "ref": "main",
  "inputs": {
    "youtube_url": "<사용자가 준 URL>",
    "transcript_path": "transcripts/{video_id}.srt",
    "model": "gemini-2.5-flash-lite"
  }
}
```

> SRT 파일은 Actions 완료 후 자동 삭제됩니다.

---

### 트리거 후 사용자 안내 메시지

```
✅ 요약 작업을 시작했습니다!
- 영상 분석 및 Notion 업로드까지 약 3~5분 소요됩니다.
- 완료되면 아래 Notion 페이지에서 확인하세요:
  https://www.notion.so/341cb54fadb680808eefd0cd2735faad
- 진행 상황: https://github.com/cultivo-hy/summary-images/actions
```

---

## video_id 추출 방법

YouTube URL에서 video_id를 추출합니다.

- `https://www.youtube.com/watch?v=ABC123` → `ABC123`
- `https://youtu.be/ABC123` → `ABC123`

---

## GitHub Secrets 등록 위치

https://github.com/cultivo-hy/summary-images/settings/secrets/actions

| Secret | 값 |
|---|---|
| `GOOGLE_API_KEY` | Gemini API Key |
| `NOTION_TOKEN` | Notion Integration Token |
| `NOTION_PAGE_ID` | `341cb54fadb680808eefd0cd2735faad` |

※ `GITHUB_TOKEN`은 Actions에서 자동 제공 (등록 불필요)
