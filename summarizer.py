#!/usr/bin/env python3
"""
YouTube Summarizer — Gemini 버전 (이미지 포함)
- YouTube URL을 Gemini에 직접 전달 → 영상 분석 + 타임스탬프 추출
- 타임스탬프 기반으로 ffmpeg 프레임 추출 → 마크다운에 이미지 삽입
- 자막 SRT 파일 선택적 제공 (정확도 향상)
- 무료 티어로 동작 (GOOGLE_API_KEY만 있으면 됨)
"""

import os
import re
import json
import subprocess
import sys
import requests
from dataclasses import dataclass, field
from typing import Optional

from google import genai
from google.genai import types


# ── 설정 ──────────────────────────────────────────────────────────────────────

@dataclass
class Config:
    model: str = "gemini-2.5-flash"
    output_path: Optional[str] = None
    languages: list = field(default_factory=lambda: ["ko", "en", "ja"])
    # 프레임 추출 화질 (480p: 슬라이드 충분, 720p: 더 선명)
    video_quality: str = "best[height<=480]/worst"
    # ffmpeg 이미지 품질 (2=고품질, 5=저용량)
    jpeg_quality: int = 3
    # 프레임 추출 여부 (False면 텍스트만)
    extract_frames: bool = True
    # GitHub 이미지 업로드 설정 (설정 시 raw URL → Notion 호환)
    github_token: Optional[str] = None
    github_repo: Optional[str] = None  # "username/repo-name"


# ── 유틸 ──────────────────────────────────────────────────────────────────────

def extract_video_id(url: str) -> str:
    patterns = [r"(?:v=|youtu\.be/|embed/|shorts/)([A-Za-z0-9_-]{11})"]
    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            return match.group(1)
    raise ValueError(f"YouTube video ID를 찾을 수 없습니다: {url}")


def format_time(seconds: float) -> str:
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def time_str_to_seconds(time_str: str) -> float:
    """HH:MM:SS → 초"""
    parts = time_str.strip().split(":")
    if len(parts) == 3:
        return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
    elif len(parts) == 2:
        return int(parts[0]) * 60 + float(parts[1])
    return float(parts[0])


def log(msg: str):
    print(f"  {msg}")


# ── 메타데이터 ────────────────────────────────────────────────────────────────

def fetch_metadata(video_id: str) -> dict:
    log("영상 메타데이터 조회 중...")
    try:
        result = subprocess.run(
            ["yt-dlp", "--dump-json", "--no-playlist",
             "--extractor-args", "youtubepot-bgutilhttp:base_url=http://127.0.0.1:4416",
             f"https://www.youtube.com/watch?v={video_id}"],
            capture_output=True, text=True, timeout=30
        )
        if result.returncode != 0:
            raise RuntimeError(result.stderr[:200])
        data = json.loads(result.stdout)
        return {
            "title": data.get("title", video_id),
            "channel": data.get("uploader", ""),
            "duration": data.get("duration", 0),
            "upload_date": data.get("upload_date", ""),
            "view_count": data.get("view_count", 0),
            "description": (data.get("description") or "")[:500],
        }
    except Exception as e:
        log(f"메타데이터 조회 실패 ({e}) → 기본값 사용")
        return {"title": video_id, "channel": "", "duration": 0,
                "upload_date": "", "view_count": 0, "description": ""}


# ── SRT 파서 ──────────────────────────────────────────────────────────────────

def parse_srt(srt_path: str) -> str:
    log(f"SRT 파일 파싱 중: {srt_path}")

    def srt_time_to_seconds(t: str) -> float:
        t = t.strip().replace(",", ".")
        h, m, rest = t.split(":")
        return int(h) * 3600 + int(m) * 60 + float(rest)

    with open(srt_path, encoding="utf-8-sig") as f:
        content = f.read()

    blocks = re.split(r"\n\s*\n", content.strip())
    lines_out = []
    for block in blocks:
        lines = block.strip().splitlines()
        if len(lines) < 3 or not lines[0].strip().isdigit():
            continue
        tc_match = re.match(
            r"(\d{2}:\d{2}:\d{2}[,\.]\d{3})\s*-->\s*(\d{2}:\d{2}:\d{2}[,\.]\d{3})",
            lines[1]
        )
        if not tc_match:
            continue
        start = srt_time_to_seconds(tc_match.group(1))
        text = " ".join(lines[2:]).strip()
        if text:
            lines_out.append(f"[{format_time(start)}] {text}")

    log(f"SRT 파싱 완료: {len(lines_out)}개 항목")
    return "\n".join(lines_out)


# ── YouTube 자막 가져오기 ─────────────────────────────────────────────────────

def fetch_transcript_text(video_id: str, languages: list) -> str:
    try:
        from youtube_transcript_api import YouTubeTranscriptApi
        log(f"YouTube 자막 가져오는 중... (언어: {languages})")
        try:
            ytt = YouTubeTranscriptApi()
            transcript = ytt.fetch(video_id, languages=languages)
        except Exception:
            tl = YouTubeTranscriptApi().list(video_id)
            transcript = next(iter(tl)).fetch()
        lines = [f"[{format_time(t.start)}] {t.text}" for t in transcript]
        log(f"자막 로드 완료: {len(lines)}개 항목")
        return "\n".join(lines)
    except Exception as e:
        log(f"자막 가져오기 실패: {e}")
        return ""


# ── Gemini 요약 생성 ──────────────────────────────────────────────────────────

def build_prompt(metadata: dict, transcript_text: str) -> str:
    duration_str = format_time(metadata.get("duration", 0))
    transcript_section = ""
    if transcript_text:
        transcript_section = f"""
## 참고 자막 (타임스탬프 포함)
아래 자막을 참고하여 더 정확하게 요약해주세요.
{transcript_text[:8000]}
"""
    return f"""다음 YouTube 영상을 분석하고 한국어 마크다운으로 상세하게 요약해줘.

영상 정보:
- 제목: {metadata.get('title', '')}
- 채널: {metadata.get('channel', '')}
- 길이: {duration_str}
{transcript_section}

다음 형식으로 작성해줘:

## 핵심 요약
(3~5문장으로 영상 전체를 빠르게 파악할 수 있도록)

## 주요 내용
(섹션별로 나눠서 정리, 각 섹션에 관련 타임스탬프 `[HH:MM:SS]` 포함)

## 핵심 키워드 / 개념
(bullet list)

## 인상적인 포인트 또는 결론

## 🎞 타임스탬프별 장면
각 장면을 아래 형식으로 작성해줘. 타임스탬프는 반드시 `### [HH:MM:SS]` 형식으로 시작해야 해:

### [HH:MM:SS]
**화면**: (슬라이드, 도표, 코드, 사람 등 화면에 보이는 것 설명)
**내용**: (해당 장면에서 설명하는 핵심 내용)

타임스탬프는 `[HH:MM:SS]` 형식으로 표기해줘.
마크다운 코드블록 없이 마크다운 자체로 반환해줘."""


def generate_summary(client, url: str, metadata: dict,
                     transcript_text: str, model: str) -> str:
    log(f"Gemini ({model})로 영상 분석 중...")
    log("YouTube URL 직접 전달 — 영상 다운로드 없음")

    video_id = extract_video_id(url)
    youtube_url = f"https://www.youtube.com/watch?v={video_id}"
    prompt = build_prompt(metadata, transcript_text)

    response = client.models.generate_content(
        model=model,
        contents=types.Content(
            parts=[
                types.Part(file_data=types.FileData(file_uri=youtube_url)),
                types.Part(text=prompt),
            ]
        ),
    )
    return response.text.strip()


# ── 타임스탬프 파싱 ───────────────────────────────────────────────────────────

def parse_scene_timestamps(summary: str, max_seconds: float = 0) -> list[float]:
    """
    Gemini 응답에서 '### [HH:MM:SS]' 형식 타임스탬프 추출.
    - max_seconds > 0 이면 영상 길이 초과 타임스탬프 제거
    - HH가 00이 아닌 경우 (예: 17:52:00) 도 제거 (24분 영상에 17시간은 불가)
    """
    pattern = r"###\s*\[(\d{2}:\d{2}:\d{2})\]"
    matches = re.findall(pattern, summary)
    timestamps = []
    seen = set()
    skipped = 0
    for ts in matches:
        sec = time_str_to_seconds(ts)
        if sec in seen:
            continue
        if max_seconds > 0 and sec > max_seconds:
            skipped += 1
            continue
        timestamps.append(sec)
        seen.add(sec)
    if skipped:
        log(f"영상 길이 초과 타임스탬프 {skipped}개 제거")
    log(f"장면 타임스탬프 {len(timestamps)}개 파싱 완료")
    return timestamps


# ── 영상 다운로드 & 프레임 추출 ───────────────────────────────────────────────

def download_video(video_id: str, out_path: str, quality: str):
    log(f"영상 다운로드 중 (화질: {quality})...")
    result = subprocess.run(
        [
            "yt-dlp",
            "-f", quality,
            "--remote-components", "ejs:github",
            "--no-check-certificates",
            "--no-playlist",
            "--extractor-args", "youtubepot-bgutilhttp:base_url=http://127.0.0.1:4416",
            "-o", out_path,
            f"https://www.youtube.com/watch?v={video_id}",
        ],
        capture_output=True, text=True, timeout=300
    )
    if result.returncode != 0:
        raise RuntimeError(f"영상 다운로드 실패:\n{result.stderr[:300]}")
    log("다운로드 완료")


def extract_frame(video_path: str, timestamp: float,
                  out_path: str, quality: int) -> bool:
    """지정 타임스탬프에서 프레임 1장 추출"""
    result = subprocess.run(
        [
            "ffmpeg",
            "-ss", f"{timestamp:.3f}",
            "-i", video_path,
            "-frames:v", "1",
            "-q:v", str(quality),
            "-y", out_path,
        ],
        capture_output=True, timeout=30
    )
    return result.returncode == 0 and os.path.exists(out_path)


def extract_all_frames(video_id: str, timestamps: list[float],
                       frames_dir: str, video_quality: str,
                       jpeg_quality: int) -> dict[float, str]:
    """
    타임스탬프 목록에 해당하는 프레임을 모두 추출.
    캐시 폴더에 저장하며, 이미 있으면 재사용.
    반환: {timestamp: frame_path}
    """
    os.makedirs(frames_dir, exist_ok=True)
    video_path = os.path.join(frames_dir, "..", "video.mp4")
    video_path = os.path.abspath(video_path)

    # 필요한 프레임 중 아직 없는 것만 추출
    needed = []
    frame_map = {}
    for ts in timestamps:
        ts_tag = f"{int(ts):05d}"
        frame_path = os.path.join(frames_dir, f"frame_{ts_tag}s.jpg")
        frame_map[ts] = frame_path
        if not os.path.exists(frame_path):
            needed.append(ts)

    if not needed:
        log(f"캐시된 프레임 {len(timestamps)}장 재사용")
        return frame_map

    # 영상 다운로드 (아직 없을 때만)
    if not os.path.exists(video_path):
        download_video(video_id, video_path, video_quality)

    log(f"프레임 {len(needed)}장 추출 중...")
    for ts in needed:
        frame_path = frame_map[ts]
        ok = extract_frame(video_path, ts, frame_path, jpeg_quality)
        if not ok:
            log(f"  ⚠ {format_time(ts)} 추출 실패, 건너뜀")
            frame_map.pop(ts, None)

    # 영상 파일 삭제 (프레임은 캐시에 보존)
    if os.path.exists(video_path):
        os.remove(video_path)
        log("영상 파일 삭제 완료 (프레임은 캐시에 보존)")

    log(f"프레임 추출 완료: {len(frame_map)}장")
    return frame_map


# ── 마크다운에 이미지 삽입 ────────────────────────────────────────────────────

def embed_frames_in_summary(summary: str, frame_map: dict[float, str],
                             output_dir: str, video_id: str) -> str:
    """
    summary 텍스트에서 '### [HH:MM:SS]' 를 찾아
    바로 아래에 이미지 링크 삽입.
    - GitHub URL이면 그대로 사용
    - 로컬 경로면 output_path 기준 상대경로로 변환
    """
    def replace_scene(match):
        ts_str = match.group(1)           # "HH:MM:SS"
        ts_sec = time_str_to_seconds(ts_str)
        yt_link = f"https://www.youtube.com/watch?v={video_id}&t={int(ts_sec)}s"
        heading = f"### [{ts_str}]({yt_link})"

        # 가장 가까운 타임스탬프의 프레임 찾기 (60초 이내)
        closest = min(frame_map.keys(), key=lambda t: abs(t - ts_sec), default=None)
        if closest is not None and abs(closest - ts_sec) < 60:
            frame_path = frame_map[closest]
            # GitHub URL이면 그대로, 로컬이면 상대경로로 변환
            if frame_path.startswith("http"):
                img_ref = frame_path
            else:
                img_ref = os.path.relpath(frame_path, output_dir)
            img_tag = f"\n![{ts_str}]({img_ref})"
            return heading + img_tag
        return heading

    pattern = r"###\s*\[(\d{2}:\d{2}:\d{2})\]"
    return re.sub(pattern, replace_scene, summary)


# ── GitHub 이미지 업로드 ──────────────────────────────────────────────────────

def upload_to_github(image_path: str, token: str, repo: str, video_id: str) -> Optional[str]:
    """
    이미지를 GitHub repo에 업로드하고 raw URL 반환.
    실패 시 None 반환.
    repo: "username/repo-name"
    """
    import base64
    filename = os.path.basename(image_path)
    remote_path = f"frames/{video_id}/{filename}"
    api_url = f"https://api.github.com/repos/{repo}/contents/{remote_path}"

    try:
        with open(image_path, "rb") as f:
            content = base64.b64encode(f.read()).decode("utf-8")

        # 파일이 이미 있으면 SHA 필요 (덮어쓰기)
        headers = {"Authorization": f"token {token}", "Accept": "application/vnd.github.v3+json"}
        check = requests.get(api_url, headers=headers, timeout=10)
        sha = check.json().get("sha") if check.status_code == 200 else None

        payload = {"message": f"Add frame {filename}", "content": content}
        if sha:
            payload["sha"] = sha

        response = requests.put(api_url, headers=headers, json=payload, timeout=30)
        if response.status_code in (200, 201):
            raw_url = f"https://raw.githubusercontent.com/{repo}/main/{remote_path}"
            return raw_url
        else:
            log(f"  GitHub 업로드 실패: {response.status_code}")
            return None
    except Exception as e:
        log(f"  GitHub 업로드 오류: {e}")
        return None


def upload_frames_to_github(frame_map: dict, token: str, repo: str, video_id: str) -> dict:
    """
    frame_map의 모든 이미지를 GitHub에 업로드.
    반환: {timestamp: raw_url} (실패한 항목은 로컬 경로 유지)
    """
    log(f"GitHub({repo})에 이미지 {len(frame_map)}장 업로드 중...")
    result = {}
    for i, (ts, local_path) in enumerate(frame_map.items()):
        log(f"  [{i+1}/{len(frame_map)}] {format_time(ts)} 업로드 중...")
        url = upload_to_github(local_path, token, repo, video_id)
        result[ts] = url if url else local_path
    success = sum(1 for v in result.values() if v.startswith("https://raw.githubusercontent.com"))
    log(f"GitHub 업로드 완료: {success}/{len(frame_map)}장 성공")
    return result


# ── 마크다운 파일 조립 ────────────────────────────────────────────────────────

def assemble_markdown(metadata: dict, summary: str, video_id: str,
                      output_path: str, frame_map: dict = None):
    upload_date = metadata.get("upload_date", "")
    if len(upload_date) == 8:
        upload_date = f"{upload_date[:4]}.{upload_date[4:6]}.{upload_date[6:]}"
    view_count = metadata.get("view_count", 0)
    view_str = f"{view_count:,}" if view_count else "알 수 없음"

    # 이미지 삽입
    output_dir = os.path.dirname(output_path)
    if frame_map:
        summary = embed_frames_in_summary(summary, frame_map, output_dir, video_id)

    lines = [
        f"# {metadata['title']}",
        "",
        f"> **채널**: {metadata['channel']}  ",
        f"> **업로드**: {upload_date}  ",
        f"> **길이**: {format_time(metadata['duration'])}  ",
        f"> **조회수**: {view_str}  ",
        f"> **URL**: https://www.youtube.com/watch?v={video_id}",
        "",
        "---",
        "",
        summary,
        "",
        "---",
        "",
        "*이 문서는 Google Gemini API로 자동 생성되었습니다.*",
    ]
    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    log(f"마크다운 저장 완료: {output_path}")


# ── 메인 파이프라인 ───────────────────────────────────────────────────────────

def summarize_youtube(url: str, cfg: Config = None, transcript_path: str = None) -> str:
    """
    전체 파이프라인 실행.

    인증: GOOGLE_API_KEY 환경변수 설정 필요
    발급: https://aistudio.google.com → Get API Key (무료, 카드 불필요)
    """
    if cfg is None:
        cfg = Config()

    api_key = os.environ.get("GOOGLE_API_KEY")
    if not api_key:
        raise RuntimeError(
            "GOOGLE_API_KEY 환경변수가 없습니다.\n"
            "  1. https://aistudio.google.com 접속\n"
            "  2. 좌측 'Get API Key' → 'Create API Key'\n"
            "  3. export GOOGLE_API_KEY='AIza...'"
        )

    client = genai.Client(api_key=api_key)

    total_steps = 5 if cfg.extract_frames else 4
    print(f"\n{'='*60}")
    print(f"🎬 YouTube Summarizer (Gemini + 이미지) 시작")
    print(f"{'='*60}")

    # 1. video ID 추출
    video_id = extract_video_id(url)
    print(f"\n[1/{total_steps}] Video ID: {video_id}")

    # 2. 메타데이터
    print(f"\n[2/{total_steps}] 메타데이터 수집")
    metadata = fetch_metadata(video_id)
    log(f"제목: {metadata['title']}")
    log(f"채널: {metadata['channel']} | 길이: {format_time(metadata['duration'])}")

    # 3. 자막 (선택)
    print(f"\n[3/{total_steps}] 자막 로드")
    if transcript_path:
        log(f"자막 소스: SRT 파일 ({transcript_path})")
        transcript_text = parse_srt(transcript_path)
    else:
        log("자막 소스: YouTube 자막 API 자동 시도")
        transcript_text = fetch_transcript_text(video_id, cfg.languages)
        if not transcript_text:
            log("자막 없음 → Gemini가 영상 자체를 분석합니다")

    # 4. Gemini 요약
    print(f"\n[4/{total_steps}] Gemini 영상 분석 & 요약 생성")
    summary = generate_summary(client, url, metadata, transcript_text, cfg.model)

    # 출력 경로
    safe_title = re.sub(r'[\\/*?:"<>|]', "_", metadata["title"])[:60]
    output_path = cfg.output_path or f"{safe_title}.md"
    output_path = os.path.abspath(output_path)

    # 5. 프레임 추출 & 이미지 삽입
    frame_map = {}
    if cfg.extract_frames:
        print(f"\n[5/{total_steps}] 프레임 추출 & 이미지 삽입")
        timestamps = parse_scene_timestamps(summary, max_seconds=metadata.get("duration", 0))
        if timestamps:
            cache_dir = os.path.join(
                os.getcwd(), ".cache", "yt_summarizer", video_id
            )
            frames_dir = os.path.join(cache_dir, "frames")
            try:
                frame_map = extract_all_frames(
                    video_id, timestamps, frames_dir,
                    cfg.video_quality, cfg.jpeg_quality
                )
                # GitHub 토큰이 있으면 업로드 → raw URL로 교체
                if frame_map and cfg.github_token and cfg.github_repo:
                    frame_map = upload_frames_to_github(
                        frame_map, cfg.github_token, cfg.github_repo, video_id
                    )
                    gh_count = sum(1 for v in frame_map.values() if v.startswith("https://raw.githubusercontent.com"))
                    log(f"GitHub raw URL {gh_count}장 적용 → Notion 업로드 시 이미지 유지됨")
                else:
                    log(f"이미지 {len(frame_map)}장 마크다운에 삽입 (로컬 경로)")
            except Exception as e:
                log(f"프레임 추출 실패 ({e}) → 이미지 없이 저장")
        else:
            log("타임스탬프를 찾지 못함 → 이미지 없이 저장")

    assemble_markdown(metadata, summary, video_id, output_path, frame_map)

    print(f"\n{'='*60}")
    print(f"✅ 완료!")
    print(f"   출력 파일: {output_path}")
    print(f"   모델: {cfg.model}")
    if frame_map:
        print(f"   삽입 이미지: {len(frame_map)}장")
        print(f"   프레임 캐시: .cache/yt_summarizer/{video_id}/frames/")
    print(f"{'='*60}\n")
    return output_path


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(
        description="YouTube 영상을 Gemini로 요약합니다 (이미지 포함)"
    )
    parser.add_argument("url", help="YouTube URL")
    parser.add_argument("-o", "--output", help="출력 파일 경로 (기본: 영상제목.md)")
    parser.add_argument("--transcript", help="SRT 자막 파일 경로 (없으면 자동 시도)")
    parser.add_argument("--model", default="gemini-2.5-flash",
                        help="Gemini 모델 (기본: gemini-2.5-flash)")
    parser.add_argument("--quality", default="best[height<=480]/worst",
                        help="영상 화질 (기본: 480p, 예: best[height<=720]/worst)")
    parser.add_argument("--no-frames", action="store_true",
                        help="프레임 추출 없이 텍스트만 생성")
    parser.add_argument("--github-token",
                        default=os.environ.get("GITHUB_TOKEN"),
                        help="GitHub Personal Access Token (이미지 업로드용)")
    parser.add_argument("--github-repo",
                        default=os.environ.get("GITHUB_REPO"),
                        help="GitHub repo (예: cultivo-hy/summary-images)")
    args = parser.parse_args()

    if args.transcript and not os.path.exists(args.transcript):
        print(f"\n❌ SRT 파일을 찾을 수 없습니다: {args.transcript}", file=sys.stderr)
        sys.exit(1)

    cfg = Config(
        model=args.model,
        output_path=args.output,
        video_quality=args.quality,
        extract_frames=not args.no_frames,
        github_token=args.github_token,
        github_repo=args.github_repo,
    )
    try:
        summarize_youtube(args.url, cfg, transcript_path=args.transcript)
    except Exception as e:
        print(f"\n❌ 오류: {e}", file=sys.stderr)
        sys.exit(1)
