#!/usr/bin/env python3
"""
summary_output.md → Notion 페이지 업로드
환경변수:
  NOTION_TOKEN   : Notion Integration Token
  NOTION_PAGE_ID : 업로드할 부모 페이지 ID
"""

import os
import sys
import re
import requests

NOTION_TOKEN   = os.environ["NOTION_TOKEN"]
NOTION_PAGE_ID = os.environ["NOTION_PAGE_ID"]
HEADERS = {
    "Authorization": f"Bearer {NOTION_TOKEN}",
    "Content-Type": "application/json",
    "Notion-Version": "2022-06-28",
}


def md_to_notion_blocks(md: str) -> list:
    """마크다운을 Notion 블록 리스트로 변환 (간단 구현)"""
    blocks = []
    lines = md.split("\n")
    i = 0

    while i < len(lines):
        line = lines[i]

        # 이미지 ![alt](url)
        img_match = re.match(r"!\[.*?\]\((https?://[^\)]+)\)", line)
        if img_match:
            url = img_match.group(1)
            blocks.append({
                "object": "block",
                "type": "image",
                "image": {"type": "external", "external": {"url": url}},
            })
            i += 1
            continue

        # H1
        if line.startswith("# ") and not line.startswith("## "):
            blocks.append({
                "object": "block",
                "type": "heading_1",
                "heading_1": {"rich_text": [{"type": "text", "text": {"content": line[2:]}}]},
            })

        # H2
        elif line.startswith("## "):
            blocks.append({
                "object": "block",
                "type": "heading_2",
                "heading_2": {"rich_text": [{"type": "text", "text": {"content": line[3:]}}]},
            })

        # H3
        elif line.startswith("### "):
            # 타임스탬프 링크 처리 ### [HH:MM:SS](url)
            ts_match = re.match(r"### \[(\d{2}:\d{2}:\d{2})\]\((https?://[^\)]+)\)", line)
            if ts_match:
                ts, url = ts_match.group(1), ts_match.group(2)
                blocks.append({
                    "object": "block",
                    "type": "heading_3",
                    "heading_3": {"rich_text": [
                        {"type": "text", "text": {"content": ts},
                         "annotations": {"bold": True}},
                        {"type": "text", "text": {"content": " ", "link": {"url": url}}},
                    ]},
                })
            else:
                blocks.append({
                    "object": "block",
                    "type": "heading_3",
                    "heading_3": {"rich_text": [{"type": "text", "text": {"content": line[4:]}}]},
                })

        # 구분선
        elif line.strip() == "---":
            blocks.append({"object": "block", "type": "divider", "divider": {}})

        # 불릿 리스트
        elif line.startswith("* ") or line.startswith("- "):
            text = line[2:].strip()
            # **bold** 처리
            rich = parse_inline(text)
            blocks.append({
                "object": "block",
                "type": "bulleted_list_item",
                "bulleted_list_item": {"rich_text": rich},
            })

        # 번호 리스트
        elif re.match(r"^\d+\. ", line):
            text = re.sub(r"^\d+\. ", "", line)
            blocks.append({
                "object": "block",
                "type": "numbered_list_item",
                "numbered_list_item": {"rich_text": parse_inline(text)},
            })

        # 인용 (> 로 시작)
        elif line.startswith("> "):
            blocks.append({
                "object": "block",
                "type": "quote",
                "quote": {"rich_text": [{"type": "text", "text": {"content": line[2:]}}]},
            })

        # 빈 줄
        elif line.strip() == "":
            pass

        # 일반 단락
        else:
            if line.strip():
                blocks.append({
                    "object": "block",
                    "type": "paragraph",
                    "paragraph": {"rich_text": parse_inline(line)},
                })

        i += 1

    return blocks


def parse_inline(text: str) -> list:
    """**bold**, [link](url) 등 인라인 마크다운을 rich_text로 변환"""
    rich = []
    # **bold** 처리
    parts = re.split(r"(\*\*[^*]+\*\*)", text)
    for part in parts:
        if part.startswith("**") and part.endswith("**"):
            rich.append({
                "type": "text",
                "text": {"content": part[2:-2]},
                "annotations": {"bold": True},
            })
        elif part:
            # [text](url) 링크 처리
            link_parts = re.split(r"(\[[^\]]+\]\(https?://[^\)]+\))", part)
            for lp in link_parts:
                lm = re.match(r"\[([^\]]+)\]\((https?://[^\)]+)\)", lp)
                if lm:
                    rich.append({
                        "type": "text",
                        "text": {"content": lm.group(1), "link": {"url": lm.group(2)}},
                    })
                elif lp:
                    rich.append({"type": "text", "text": {"content": lp}})
    return rich if rich else [{"type": "text", "text": {"content": text}}]


def get_title_from_md(md: str) -> str:
    """마크다운 첫 번째 # 제목 추출"""
    for line in md.split("\n"):
        if line.startswith("# "):
            return line[2:].strip()
    return "YouTube 요약"


def upload_to_notion(md_path: str):
    with open(md_path, encoding="utf-8") as f:
        md = f.read()

    title = get_title_from_md(md)
    blocks = md_to_notion_blocks(md)

    # Notion API는 한 번에 100 블록까지만 허용
    # 페이지 생성 (첫 100 블록)
    payload = {
        "parent": {"page_id": NOTION_PAGE_ID},
        "properties": {"title": [{"type": "text", "text": {"content": title}}]},
        "children": blocks[:100],
    }
    resp = requests.post(
        "https://api.notion.com/v1/pages",
        headers=HEADERS,
        json=payload,
        timeout=30,
    )
    resp.raise_for_status()
    page_id = resp.json()["id"]
    print(f"✅ Notion 페이지 생성: {title}")
    print(f"   URL: https://www.notion.so/{page_id.replace('-', '')}")

    # 100 블록 초과 시 추가 append
    for chunk_start in range(100, len(blocks), 100):
        chunk = blocks[chunk_start:chunk_start + 100]
        resp = requests.patch(
            f"https://api.notion.com/v1/blocks/{page_id}/children",
            headers=HEADERS,
            json={"children": chunk},
            timeout=30,
        )
        resp.raise_for_status()
        print(f"   블록 추가: {chunk_start}~{chunk_start + len(chunk)}")

    print(f"✅ 총 {len(blocks)}개 블록 업로드 완료")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python upload_to_notion.py <md_file>")
        sys.exit(1)
    upload_to_notion(sys.argv[1])
