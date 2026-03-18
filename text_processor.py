import os
import re

from google import genai


class TextProcessor:
    def __init__(self):
        api_key = os.environ.get("GEMINI_API_KEY")
        if not api_key:
            raise ValueError("GEMINI_API_KEY が設定されていません。.env を確認してください。")
        self.client = genai.Client(api_key=api_key)
        self.model_name = "gemini-2.5-flash"

    def process_for_voicevox(self, text: str) -> str:
        """Gemini APIを使ってVOICEVOX用にテキストを整形"""
        prompt = (
            "以下の怪談テキストを、音声合成ソフト（VOICEVOX）で"
            "正確に読み上げられるように加工してください。\n\n"
            "ルール:\n"
            "- 漢字の読みが曖昧なものはひらがなに変換してください"
            "（例: 強面→こわもて、明後日→あさって）\n"
            "- 一般的な漢字はそのまま残してOKです（例: 私、家、夜、神社）\n"
            "- 句読点はそのまま保持してください\n"
            "- 改行はそのまま保持してください\n"
            "- 不要な記号や装飾文字は削除してください\n"
            "- 文章の内容は一切変えないでください\n"
            "- 説明や注釈は付けず、加工後のテキストのみを出力してください\n\n"
            f"テキスト:\n{text}"
        )

        response = self.client.models.generate_content(model=self.model_name, contents=prompt)
        processed = response.text.strip()

        # コードブロックが含まれる場合は除去
        if processed.startswith("```"):
            processed = re.sub(r"^```\w*\n?", "", processed)
            processed = re.sub(r"\n?```$", "", processed)

        return processed

    def split_into_chunks(self, text: str, max_length: int = 200) -> list[str]:
        """長いテキストを適切な長さに分割"""
        sentences = re.split(r"[。！？]", text)
        chunks = []
        current_chunk = ""

        for sentence in sentences:
            sentence = sentence.strip()
            if not sentence:
                continue

            sentence += "。"

            if len(current_chunk) + len(sentence) <= max_length:
                current_chunk += sentence
            else:
                if current_chunk:
                    chunks.append(current_chunk)
                current_chunk = sentence

        if current_chunk:
            chunks.append(current_chunk)

        return chunks
