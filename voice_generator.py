import json
import os
import time
import wave

import requests


class VoiceGenerator:
    def __init__(self, host: str | None = None):
        """
        VOICEVOXエンジンの初期化
        ※ VOICEVOXがローカルで起動している必要があります
        """
        self.host = host or os.environ.get("VOICEVOX_HOST", "http://localhost:50021")
        self.speakers = self.get_speakers()

    def get_speakers(self) -> dict | None:
        """利用可能なスピーカー（声）のリストを取得"""
        try:
            response = requests.get(f"{self.host}/speakers")
            if response.status_code == 200:
                return response.json()
        except requests.exceptions.ConnectionError:
            print("⚠️  VOICEVOXエンジンに接続できません。")
            print("⚠️  VOICEVOXを起動してください：")
            print("⚠️  https://voicevox.hiroshiba.jp/")
        return None

    def create_audio_query(self, text: str, speaker_id: int = 1) -> dict | None:
        """音声合成用のクエリを作成"""
        params = {"text": text, "speaker": speaker_id}

        try:
            response = requests.post(f"{self.host}/audio_query", params=params)
            if response.status_code == 200:
                return response.json()
        except Exception as e:
            print(f"エラー: {e}")
        return None

    def synthesis(self, query: dict, speaker_id: int = 1) -> bytes | None:
        """音声を合成"""
        params = {"speaker": speaker_id}
        headers = {"Content-Type": "application/json"}

        try:
            response = requests.post(
                f"{self.host}/synthesis", params=params, headers=headers, data=json.dumps(query)
            )
            if response.status_code == 200:
                return response.content
        except Exception as e:
            print(f"エラー: {e}")
        return None

    def text_to_speech(
        self,
        text: str,
        speaker_id: int = 3,
        speed: float = 0.9,
        pitch: float = 0.0,
        intonation: float = 1.1,
    ) -> bytes | None:
        """
        テキストから音声を生成

        speaker_id:
          1: あかり(ノーマル)
          2: りつ(ノーマル)
          3: ななみ(ノーマル) - 怪談向き
          8: 春日部つむぎ(ノーマル)
          11: 雨晴はう(ノーマル)
          13: 青山龍星(ノーマル)
        """
        # クエリ作成
        query = self.create_audio_query(text, speaker_id)
        if query is None:
            return None

        # パラメータ調整
        query["speedScale"] = speed  # 話す速度（怪談はゆっくり目）
        query["pitchScale"] = pitch  # ピッチ
        query["intonationScale"] = intonation  # 抑揚（怪談は強め）
        query["volumeScale"] = 1.0  # 音量

        # 音声合成
        return self.synthesis(query, speaker_id)

    def save_audio(self, audio_data: bytes, filename: str):
        """音声データをファイルに保存"""
        with open(filename, "wb") as f:
            f.write(audio_data)
        print(f"保存完了: {filename}")

    def generate_narration(
        self, text_chunks: list[str], output_dir: str = "audio", speaker_id: int = 3
    ) -> list[str]:
        """
        複数のテキストチャンクからナレーションを生成
        """
        os.makedirs(output_dir, exist_ok=True)
        audio_files = []

        for i, chunk in enumerate(text_chunks):
            print(f"音声生成中... ({i + 1}/{len(text_chunks)})")

            # 音声生成
            audio_data = self.text_to_speech(chunk, speaker_id=speaker_id)

            if audio_data:
                filename = os.path.join(output_dir, f"narration_{i:04d}.wav")
                self.save_audio(audio_data, filename)
                audio_files.append(filename)

                # API負荷軽減のため少し待つ
                time.sleep(0.5)

        return audio_files

    def concatenate_audio(self, audio_files: list[str], output_file: str):
        """複数の音声ファイルを結合"""
        if not audio_files:
            return

        # 最初のファイルのパラメータを取得
        with wave.open(audio_files[0], "rb") as w:
            params = w.getparams()
            frames = []

            # すべてのファイルを読み込み
            for audio_file in audio_files:
                with wave.open(audio_file, "rb") as wav_file:
                    frames.append(wav_file.readframes(wav_file.getnframes()))

        # 結合した音声を保存
        with wave.open(output_file, "wb") as output:
            output.setparams(params)
            for frame in frames:
                output.writeframes(frame)

        print(f"結合完了: {output_file}")
