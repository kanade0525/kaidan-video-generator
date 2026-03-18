import re
import time

import requests
from bs4 import BeautifulSoup


class KaidanScraper:
    def __init__(self):
        self.base_url = "https://hhs.parasite.jp"
        self.session = requests.Session()
        self.session.headers.update(
            {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
        )

    def get_story_list(self) -> list[dict[str, str]]:
        """怪談のリストを取得"""
        response = self.session.get(self.base_url)
        soup = BeautifulSoup(response.content, "html.parser")

        stories = []
        links = soup.find_all("a", href=re.compile(r"/story/\d+"))

        for link in links:
            story_info = {"title": link.text.strip(), "url": self.base_url + link["href"]}
            stories.append(story_info)

        return stories

    def get_story_content(self, story_url: str) -> dict[str, str]:
        """個別の怪談の内容を取得"""
        response = self.session.get(story_url)
        soup = BeautifulSoup(response.content, "html.parser")

        # タイトルと本文を抽出（サイト構造に応じて調整必要）
        title = soup.find("h1") or soup.find("h2")
        title = title.text.strip() if title else "無題"

        # 本文を取得（複数のパターンに対応）
        content_selectors = [
            "div.entry-content",
            "div.story-content",
            "div.content",
            "div.main-content",
            "article",
            "main",
        ]

        content = ""
        for selector in content_selectors:
            content_elem = soup.select_one(selector)
            if content_elem:
                # タグを除去してテキストのみ取得
                content = content_elem.get_text("\n", strip=True)
                break

        if not content:
            # フォールバック: body全体から取得
            body = soup.find("body")
            if body:
                content = body.get_text("\n", strip=True)

        # 不要な改行を整理
        content = re.sub(r"\n{3,}", "\n\n", content)

        return {"title": title, "content": content, "url": story_url}

    def scrape_stories(self, limit: int = 5) -> list[dict[str, str]]:
        """指定数の怪談を取得"""
        story_list = self.get_story_list()
        results = []

        for i, story in enumerate(story_list[:limit]):
            print(f"取得中: {story['title']}")
            story_data = self.get_story_content(story["url"])
            results.append(story_data)

            # サーバー負荷軽減のため少し待つ
            if i < limit - 1:
                time.sleep(1)

        return results
