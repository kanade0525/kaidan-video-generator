import json
import os
import subprocess
import wave


class VideoGenerator:
    def __init__(self, output_dir: str = "videos"):
        """
        動画生成クラスの初期化
        ffmpegがインストールされている必要があります
        """
        self.output_dir = output_dir
        os.makedirs(self.output_dir, exist_ok=True)

        # ffmpegが利用可能かチェック
        self.check_ffmpeg()

    def check_ffmpeg(self) -> bool:
        """がインストールされているか確認"""
        try:
            subprocess.run(["ffmpeg", "-version"], capture_output=True, check=True)
            return True
        except (subprocess.CalledProcessError, FileNotFoundError):
            print("⚠️  ffmpegが見つかりません。")
            print("⚠️  インストール方法:")
            print("⚠️  macOS: brew install ffmpeg")
            print("⚠️  Ubuntu: sudo apt install ffmpeg")
            print("⚠️  Windows: https://ffmpeg.org/download.html")
            return False

    def get_audio_duration(self, audio_file: str) -> float:
        """音声ファイルの長さを取得（秒）"""
        with wave.open(audio_file, "rb") as wav_file:
            frames = wav_file.getnframes()
            rate = wav_file.getframerate()
            duration = frames / float(rate)
        return duration

    def create_image_video(self, image_path: str, duration: float, output_path: str, fps: int = 30):
        """
        静止画から指定時間の動画を作成
        """
        cmd = [
            "ffmpeg",
            "-loop",
            "1",  # 画像をループ
            "-i",
            image_path,  # 入力画像
            "-c:v",
            "libx264",  # ビデオコーデック
            "-t",
            str(duration),  # 時間
            "-pix_fmt",
            "yuv420p",  # ピクセルフォーマット
            "-r",
            str(fps),  # フレームレート
            "-y",  # 上書き
            output_path,
        ]

        subprocess.run(cmd, capture_output=True, check=True)

    def create_slideshow(
        self, images: list[str], audio_file: str, output_file: str, transition_duration: float = 2.0
    ):
        """
        複数の画像と音声からスライドショー動画を作成
        """
        # 音声の長さを取得
        audio_duration = self.get_audio_duration(audio_file)

        # 各画像の表示時間を計算
        if len(images) > 0:
            image_duration = audio_duration / len(images)
        else:
            print("画像がありません")
            return None

        # 画像リストファイルを作成
        list_file = os.path.join(self.output_dir, "images.txt")
        with open(list_file, "w") as f:
            for image in images:
                # ffmpeg concat形式
                f.write(f"file '{os.path.abspath(image)}'\n")
                f.write(f"duration {image_duration}\n")
            # 最後の画像を再度追加（ffmpegの仕様）
            f.write(f"file '{os.path.abspath(images[-1])}'\n")

        # 動画作成コマンド
        cmd = [
            "ffmpeg",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            list_file,
            "-i",
            audio_file,
            "-c:v",
            "libx264",
            "-c:a",
            "aac",
            "-pix_fmt",
            "yuv420p",
            "-shortest",  # 短い方に合わせる
            "-y",
            output_file,
        ]

        try:
            subprocess.run(cmd, capture_output=True, check=True)
            print(f"動画生成完了: {output_file}")
            return output_file
        except subprocess.CalledProcessError as e:
            print(f"動画生成エラー: {e}")
            print(f"stderr: {e.stderr.decode('utf-8')}")
            return None
        finally:
            # 一時ファイルを削除
            if os.path.exists(list_file):
                os.remove(list_file)

    def add_fade_effects(
        self, input_video: str, output_video: str, fade_in: float = 1.0, fade_out: float = 1.0
    ):
        """
        フェードイン・フェードアウト効果を追加
        """
        # 動画の長さを取得
        probe_cmd = [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "json",
            input_video,
        ]

        result = subprocess.run(probe_cmd, capture_output=True, text=True)
        duration_info = json.loads(result.stdout)
        duration = float(duration_info["format"]["duration"])

        # フェード効果を適用
        fade_out_start = duration - fade_out

        cmd = [
            "ffmpeg",
            "-i",
            input_video,
            "-vf",
            f"fade=in:0:{fade_in},fade=out:{fade_out_start}:{fade_out}",
            "-af",
            f"afade=in:st=0:d={fade_in},afade=out:st={fade_out_start}:d={fade_out}",
            "-c:v",
            "libx264",
            "-c:a",
            "aac",
            "-y",
            output_video,
        ]

        subprocess.run(cmd, capture_output=True, check=True)
        return output_video

    def create_horror_video(
        self, images: list[str], audio_file: str, title: str, add_effects: bool = True
    ) -> str | None:
        """
        怪談動画を作成するメインメソッド
        """
        # 出力ファイル名を生成
        safe_title = "".join(c for c in title if c.isalnum() or c in (" ", "-", "_"))
        safe_title = safe_title.replace(" ", "_")[:50]

        temp_output = os.path.join(self.output_dir, f"{safe_title}_temp.mp4")
        final_output = os.path.join(self.output_dir, f"{safe_title}.mp4")

        # スライドショー動画を作成
        video_path = self.create_slideshow(images, audio_file, temp_output)

        if video_path and add_effects:
            # フェード効果を追加
            self.add_fade_effects(temp_output, final_output)
            # 一時ファイルを削除
            if os.path.exists(temp_output):
                os.remove(temp_output)
            return final_output
        elif video_path:
            # 効果なしの場合はリネーム
            os.rename(temp_output, final_output)
            return final_output

        return None
