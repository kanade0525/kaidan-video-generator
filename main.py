#!/usr/bin/env python3

import argparse
import json
import os

from dotenv import load_dotenv

from image_generator import ImageGenerator
from scraper import KaidanScraper
from text_processor import TextProcessor
from video_generator import VideoGenerator
from voice_generator import VoiceGenerator


def main():
    parser = argparse.ArgumentParser(description="怪談朗読動画ジェネレータ")
    parser.add_argument("--url", type=str, help="特定の怪談URLを指定")
    parser.add_argument("--limit", type=int, default=1, help="取得する怪談の数")
    parser.add_argument("--speaker", type=int, default=3, help="VOICEVOXスピーカーID")
    parser.add_argument(
        "--no-ai-image", action="store_true", help="AI画像生成を無効化（プロシージャル背景を使用）"
    )
    parser.add_argument("--skip-video", action="store_true", help="動画生成をスキップ")
    args = parser.parse_args()

    # 環境変数を読み込み
    load_dotenv()

    print("🌙 怪談朗読動画ジェネレータを起動します...\n")

    # 1. 怪談を取得
    print("👻 怪談を取得中...")
    scraper = KaidanScraper()

    if args.url:
        story_data = scraper.get_story_content(args.url)
        stories = [story_data]
    else:
        stories = scraper.scrape_stories(limit=args.limit)

    if not stories:
        print("怪談を取得できませんでした")
        return

    # 各怪談を処理
    for i, story in enumerate(stories):
        print(f"\n📖 処理中: {story['title']} ({i + 1}/{len(stories)})")

        # 出力ディレクトリを作成
        safe_title = "".join(c for c in story["title"] if c.isalnum() or c in (" ", "-", "_"))
        safe_title = safe_title.replace(" ", "_")[:50]
        story_dir = f"output/{safe_title}"
        os.makedirs(story_dir, exist_ok=True)

        # 2. テキスト処理
        print("  📝 テキストを処理中...")
        processor = TextProcessor()
        processed_text = processor.process_for_voicevox(story["content"])
        text_chunks = processor.split_into_chunks(processed_text, max_length=200)

        # 処理済みテキストを保存
        with open(f"{story_dir}/processed_text.txt", "w", encoding="utf-8") as f:
            f.write(processed_text)

        # 3. 音声生成
        print("  🎙 音声を生成中...")
        voice_gen = VoiceGenerator()

        if voice_gen.speakers is None:
            print("  ⚠️  VOICEVOXが起動していません。スキップします")
            continue

        audio_dir = f"{story_dir}/audio"
        audio_files = voice_gen.generate_narration(
            text_chunks, output_dir=audio_dir, speaker_id=args.speaker
        )

        # 音声を結合
        final_audio = f"{story_dir}/narration_complete.wav"
        voice_gen.concatenate_audio(audio_files, final_audio)

        # 4. 画像生成
        print("  🎨 画像を生成中...")
        image_gen = ImageGenerator()

        images = image_gen.generate_images_for_story(
            story["content"], story["title"], use_api=not args.no_ai_image
        )

        # 画像をストーリーディレクトリにコピー
        import shutil

        story_images = []
        for img in images:
            img_name = os.path.basename(img)
            new_path = f"{story_dir}/{img_name}"
            shutil.copy2(img, new_path)
            story_images.append(new_path)

        # 5. 動画生成
        if not args.skip_video:
            print("  🎬 動画を生成中...")
            video_gen = VideoGenerator(output_dir=story_dir)

            video_path = video_gen.create_horror_video(
                story_images, final_audio, story["title"], add_effects=True
            )

            if video_path:
                print(f"  ✅ 動画生成完了: {video_path}")
            else:
                print("  ⚠️  動画生成に失敗しました")

        # メタデータを保存
        metadata = {
            "title": story["title"],
            "url": story["url"],
            "audio_file": final_audio,
            "images": story_images,
            "speaker_id": args.speaker,
        }

        with open(f"{story_dir}/metadata.json", "w", encoding="utf-8") as f:
            json.dump(metadata, f, ensure_ascii=False, indent=2)

        print(f"\n✨ 完成！{story_dir} に保存されました")

    print("\n🎉 すべての処理が完了しました！")


if __name__ == "__main__":
    main()
