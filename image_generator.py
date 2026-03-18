import os
import re

import openai
import requests
from PIL import Image, ImageDraw, ImageFont


class ImageGenerator:
    def __init__(self, api_key: str | None = None):
        """
        画像生成クラスの初期化
        OpenAI APIキーが必要（DALL-E使用時）
        """
        if api_key:
            openai.api_key = api_key
        self.output_dir = "images"
        os.makedirs(self.output_dir, exist_ok=True)

    def extract_key_scenes(self, text: str, num_scenes: int = 3) -> list[str]:
        """
        怪談テキストから重要なシーンを抽出
        """
        # 簡易的なキーワード抽出
        horror_keywords = [
            "幽霊",
            "怪物",
            "妖怪",
            "悪霊",
            "怨霊",
            "死霊",
            "黒い",
            "白い",
            "赤い",
            "青い",
            "女",
            "男",
            "子供",
            "老人",
            "家",
            "廊下",
            "部屋",
            "森",
            "墓",
            "地",
            "夜",
            "暗闇",
            "闇",
            "月",
            "霧",
            "雨",
            "風",
            "叫び",
            "声",
            "泣き",
            "笑い",
        ]

        scenes = []
        sentences = re.split(r"[。！？]", text)

        for sentence in sentences:
            # キーワードを含む文を優先
            if any(keyword in sentence for keyword in horror_keywords):
                if len(sentence) > 10 and len(sentence) < 100:
                    scenes.append(sentence.strip())
                    if len(scenes) >= num_scenes:
                        break

        # 足りなければ適当に選ぶ
        if len(scenes) < num_scenes:
            for sentence in sentences[: num_scenes - len(scenes)]:
                if sentence.strip() and sentence.strip() not in scenes:
                    scenes.append(sentence.strip())

        return scenes[:num_scenes]

    def generate_prompt(self, scene_text: str, title: str = "") -> str:
        """
        シーンテキストから画像生成用プロンプトを作成
        日本語のシーン内容をそのままプロンプトに含め、物語に忠実な画像を生成する
        """
        style_suffix = (
            "photorealistic, cinematic lighting, high contrast, "
            "dark atmosphere, Japanese horror style, 4k quality"
        )

        # シーンテキストを短縮（プロンプトが長すぎると品質低下）
        scene_short = scene_text[:120]
        prompt = f"{scene_short}, {style_suffix}"

        return prompt

    def generate_with_ai(self, prompt: str, size: str = "1792x1024") -> bytes | None:
        """
        AirForce API（無料・APIキー不要）で画像生成
        """
        try:
            response = requests.post(
                "https://api.airforce/v1/images/generations",
                json={"model": "z-image", "prompt": prompt, "size": size},
                timeout=120,
            )
            if response.status_code == 200:
                data = response.json()
                if data.get("data"):
                    item = data["data"][0]
                    if "url" in item:
                        img_response = requests.get(item["url"], timeout=60)
                        if img_response.status_code == 200 and len(img_response.content) > 1000:
                            return img_response.content
            print(f"画像生成APIエラー: status={response.status_code}")
        except Exception as e:
            print(f"画像生成APIエラー: {e}")
        return None

    def generate_with_dalle(self, prompt: str, size: str = "1024x1024") -> str | None:
        """
        DALL-E APIで画像生成
        """
        try:
            response = openai.Image.create(prompt=prompt, n=1, size=size)
            image_url = response["data"][0]["url"]

            # 画像をダウンロード
            image_response = requests.get(image_url)
            if image_response.status_code == 200:
                return image_response.content
        except Exception as e:
            print(f"DALL-E APIエラー: {e}")
            return None

    def create_simple_horror_bg(
        self, width: int = 1792, height: int = 1024, color_scheme: str = "dark"
    ) -> Image.Image:
        """
        シンプルなホラー風背景を生成（API不要）
        """
        img = Image.new("RGB", (width, height))
        draw = ImageDraw.Draw(img)

        if color_scheme == "dark":
            # グラデーションで暗い背景
            for i in range(height):
                gray = int(20 + (30 * (i / height)))
                draw.rectangle([(0, i), (width, i + 1)], fill=(gray, gray, gray + 5))
        elif color_scheme == "blood":
            # 血のような赤黒い背景
            for i in range(height):
                red = int(40 + (20 * (i / height)))
                draw.rectangle([(0, i), (width, i + 1)], fill=(red, 0, 0))
        elif color_scheme == "mist":
            # 霧のような灰色背景
            for i in range(height):
                gray = int(100 + (50 * (i / height)))
                draw.rectangle([(0, i), (width, i + 1)], fill=(gray, gray, gray + 10))

        return img

    def create_title_card(self, title: str, width: int = 1792, height: int = 1024) -> str:
        """
        タイトルカードを生成
        """
        img = self.create_simple_horror_bg(width, height, "dark")
        draw = ImageDraw.Draw(img)

        # フォント設定（システムフォントを使用）
        font_paths = [
            "/System/Library/Fonts/ヒラギノ角ゴシック W6.ttc",  # macOS
            "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",  # Debian/Ubuntu
            "/usr/share/fonts/noto-cjk/NotoSansCJK-Bold.ttc",  # Fedora
        ]
        font = ImageFont.load_default()
        for path in font_paths:
            try:
                font = ImageFont.truetype(path, 80)
                break
            except OSError:
                continue

        # テキストのサイズを計算
        bbox = draw.textbbox((0, 0), title, font=font)
        text_width = bbox[2] - bbox[0]
        text_height = bbox[3] - bbox[1]

        # 中央に配置
        x = (width - text_width) // 2
        y = (height - text_height) // 2

        # 影をつけてテキストを描画
        draw.text((x + 3, y + 3), title, fill=(0, 0, 0), font=font)  # 影
        draw.text((x, y), title, fill=(200, 200, 200), font=font)  # 本体

        output_path = os.path.join(self.output_dir, "title_card.png")
        img.save(output_path)
        return output_path

    def generate_images_for_story(
        self, story_text: str, title: str, use_api: bool = True
    ) -> list[str]:
        """
        怪談のための画像セットを生成

        use_api=True: Pollinations.ai（無料）で画像生成（デフォルト）
        use_api=False: プロシージャル背景のみ
        """
        generated_images = []

        # タイトルカード
        title_path = self.create_title_card(title)
        generated_images.append(title_path)

        if use_api:
            # AIで画像生成（無料API）
            scenes = self.extract_key_scenes(story_text, num_scenes=3)

            for i, scene in enumerate(scenes):
                prompt = self.generate_prompt(scene, title=title)
                print(f"  AI画像生成中 ({i + 1}/{len(scenes)}): {prompt[:60]}...")

                if i > 0:
                    import time

                    time.sleep(15)  # レート制限回避

                image_data = self.generate_with_ai(prompt)
                if image_data:
                    image_path = os.path.join(self.output_dir, f"scene_{i:02d}.png")
                    with open(image_path, "wb") as f:
                        f.write(image_data)
                    generated_images.append(image_path)
                else:
                    # フォールバック: プロシージャル背景
                    scheme = ["dark", "mist", "blood"][i % 3]
                    img = self.create_simple_horror_bg(color_scheme=scheme)
                    image_path = os.path.join(self.output_dir, f"bg_{scheme}.png")
                    img.save(image_path)
                    generated_images.append(image_path)

        else:
            # シンプルな背景画像を生成
            print("シンプル背景を生成中...")
            color_schemes = ["dark", "mist", "blood"]

            for scheme in color_schemes[:2]:
                img = self.create_simple_horror_bg(color_scheme=scheme)
                image_path = os.path.join(self.output_dir, f"bg_{scheme}.png")
                img.save(image_path)
                generated_images.append(image_path)

        return generated_images
