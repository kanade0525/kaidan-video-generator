from __future__ import annotations

from pathlib import Path

import tomllib

CONFIG_PATH = Path("data/config.toml")

_DEFAULTS = {
    "speaker_id": 47,
    "speed": 0.9,
    "pitch": 0.0,
    "intonation": 1.0,
    "volume": 2.0,
    # ── 「間」の調整 (VOICEVOX AudioQuery のフィールドにそのまま反映) ──
    # 怪談朗読は間の取り方で怖さが変わるので UI から調整できるようにしている。
    # None 相当 (負値) を入れるとそのフィールドは触らず VOICEVOX 既定のまま。
    "pre_phoneme_length": 0.1,    # 発話前の無音 (秒)
    "post_phoneme_length": 0.1,   # 発話後の無音 (秒)
    "pause_length_scale": 1.0,    # 句読点ポーズの倍率 (VOICEVOX 0.16+)
    # 改行をどう読ませるか。"period"=。(現状/長い間) / "comma"=、(短い間) /
    # "none"=区切り無しで直結。
    "voice_newline_mode": "period",
    # アクセントの自然さ。
    #   "hiragana"       = ひらがな原稿をそのまま読ませる (従来動作)
    #   "verified_kanji" = 読みを矯正した語だけかなにした「漢字混じり原稿」を
    #                      VOICEVOX に渡す。ただし audio_query のモーラ列が
    #                      ひらがな原稿と一致した場合のみ採用し、少しでも読みが
    #                      変わるチャンクはひらがな原稿へ自動フォールバックする。
    # ひらがなだけの文は OpenJTalk が語境界を推定できずアクセントが平板化する
    # ため、読みが同一であることを検証したうえで漢字を残すほうが自然に聞こえる。
    "voice_accent_mode": "verified_kanji",
    "image_model": "gemini-2.5-flash-image",
    "image_size": "1792x1024",
    "image_aspect_ratio": "16:9",
    "image_person_generation": "DONT_ALLOW",
    "image_output_mime": "image/png",
    "image_compression_quality": 90,
    "image_negative_prompt": (
        "text, letters, words, writing, captions, watermark, signature, logo, "
        "title, subtitle, label, UI, numbers, symbols, typography, font, "
        "anime, cartoon, illustration, drawing, painting, sketch, "
        "bright colors, vibrant, cheerful, happy"
    ),
    "image_guidance_scale": 7.0,
    "image_seed": 0,
    "image_enhance_prompt": False,
    "image_add_watermark": False,
    "num_scenes": 3,
    "image_style": (
        "found footage style, low quality home video camera capture, "
        "surveillance camera footage, CCTV recording, VHS tape quality, "
        "heavy noise and static grain, scan lines, signal distortion, "
        "low resolution, slightly blurry, poor lighting, "
        "infrared night vision green tint OR washed-out security camera colors, "
        "timestamp overlay aesthetic, dark corners, lens flare artifacts, "
        "Japanese horror atmosphere, creepy unsettling mood, "
        "dread, foreboding, supernatural horror, cursed imagery, "
        "absolutely no text, no letters, no words, no writing, no watermarks, no UI elements, "
        "no high quality, no professional photography, no vivid colors, no cheerful mood"
    ),
    "image_rate_limit": 15,
    "fps": 30,
    "fade_in": 1.0,
    "fade_out": 1.0,
    "bgm_path": "",
    "bgm_volume": 0.1,
    "op_path": "",
    "op_fade_out": 1.0,
    "ed_path": "",
    # 詰め合わせ動画 (Long を OP+ジングル区切り+ED で1〜2時間に束ねる長編)
    # `assets/jingle/*.mp3` 等を指す。空ならジングル無しで story 間は無音直結。
    "bundle_jingle_path": "",
    # UI ガイド表示用 (秒)。目安より短すぎ/長すぎを警告する。
    "bundle_target_duration_min": 3600,   # 1 時間
    "bundle_target_duration_max": 7200,   # 2 時間
    # YouTube に詰め合わせ動画を投稿する際のテンプレート。
    # {name}=詰め合わせ名, {chapters}=YouTube章フォーマット (00:00 タイトル) ブロック
    "bundle_youtube_title_template": "【怪談朗読】詰め合わせ｜{name}",
    "bundle_youtube_description_template": (
        "【怪談朗読 詰め合わせ】{name}\n\n"
        "百鬼朗読へようこそ。\n"
        "怪談を続けてお楽しみいただける詰め合わせ動画です。\n"
        "チャンネル登録・高評価よろしくお願いします。\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "目次\n"
        "{chapters}\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "音声: VOICEVOX\n"
        "BGM: 「Where the Light Never Speaks」松浦洋介\n"
        "(DOVA-SYNDROME: https://dova-s.jp/bgm/play22758.html)\n\n"
        "#怪談 #ホラー #朗読 #怖い話 #百鬼朗読 #怪談朗読詰め合わせ #睡眠用 #作業用BGM"
    ),
    "bundle_youtube_tags": "怪談,ホラー,朗読,怖い話,心霊,恐怖,怪談朗読,詰め合わせ,睡眠用,作業用BGM,百鬼朗読,長編",
    "text_model": "gemini-2.5-flash",
    "gemini_model": "gemini-2.5-flash-lite",
    "max_chunk": 200,
    # テキストステージで AI 校正 (Gemini) を自動適用するか。True にすると
    # 「AI校正プレビュー → 適用」の手動ステップ無しで完全自動化できる。
    # UI の手動プレビュー/差分確認フローは有効なまま残る。
    "text_ai_proofread_enabled": True,
    "text_prompt": (
        "以下の日本語テキストをVOICEVOX朗読用にひらがな化してください。\n\n"
        "【変換ルール】\n"
        "1. 漢字は文脈に合う自然な読みでひらがなに変換する\n"
        "   例: 反省→はんせい、教訓→きょうくん、話→はなし、話題→わだい、\n"
        "       銀行→ぎんこう、母→はは、一人→ひとり、今日→きょう\n"
        "2. 助詞の「は」は「わ」、助詞の「へ」は「え」に変換する（助詞以外の「は」「へ」は変えない）\n"
        "   例: 私は走った→わたしわはしった、学校へ行く→がっこうえいく\n"
        "   注意: 「反省した」「話した」「入って」の「は」「へ」は助詞ではないので変えない\n"
        "3. 助詞の「を」はそのまま「を」で出力する（「お」に置換しない）\n"
        "4. 「である」「であり」「でした」「です」「だった」などの語尾は原形を崩さない\n"
        "5. カタカナ、句読点、記号、数字、空白はすべて原文のまま保持する\n\n"
        "【出力形式の厳守事項】\n"
        "・入力の改行位置を1つ残らず保持する。段落を結合したり一行にまとめない\n"
        "・元の文字を「（）」「［］」「【】」などで併記・注釈しない（例: ❌「わ（は）」）\n"
        "・変換結果のテキストのみを出力する。前置き・後書き・解説・箇条書きは一切不要\n"
        "・マークダウン記法（**, *, #, - など）は使わない\n"
        "・入力と近い長さ・区切りを保ち、内容を勝手に要約・追加しない\n"
    ),
    "scrape_delay": 2.0,
    "youtube_category_id": "24",
    "youtube_privacy_status": "private",
    "youtube_title_template": "【怪談朗読】{title}｜怖い話・睡眠用・作業用BGM",
    "youtube_playlist_url": "https://www.youtube.com/playlist?list=PLBj7GhxNHZWufHlq6pdSboVkOfx8uNGLY",
    "youtube_description_template": (
        "【怪談朗読】{title}\n\n"
        "百鬼朗読へようこそ。\n"
        "日本各地に伝わる怪談・不思議な話・人怖を朗読でお届けします。\n"
        "チャンネル登録・高評価よろしくお願いします。\n\n"
        "▶ 怪談再生リスト: {playlist_url}\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "引用元:HHS図書館より\n"
        "「{title}」{url}\n\n"
        "音声: VOICEVOX:{speaker}\n"
        "BGM: 「Where the Light Never Speaks」松浦洋介\n"
        "(DOVA-SYNDROME: https://dova-s.jp/bgm/play22758.html)\n"
        "SE: 「オカルト系タイトルコール」Causality Sound\n"
        "(DOVA-SYNDROME: https://dova-s.jp/se/detail/1489)\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "#怪談 #ホラー #朗読 #怖い話 #ホラホリ #百鬼朗読"
    ),
    # 奇々怪々由来のストーリーが長尺に移送された場合のテンプレート。
    # HHS規約の「ホラホリ」タグは付与しない。
    "long_kikikaikai_youtube_description_template": (
        "【怪談朗読】{title}\n\n"
        "百鬼朗読へようこそ。\n"
        "日本各地に伝わる怪談・不思議な話・人怖を朗読でお届けします。\n"
        "チャンネル登録・高評価よろしくお願いします。\n\n"
        "▶ 怪談再生リスト: {playlist_url}\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "引用元: 怖い話投稿サイト 奇々怪々\n"
        "「{title}」{url}\n"
        "作者: {author}\n\n"
        "音声: VOICEVOX:{speaker}\n"
        "BGM: 「Where the Light Never Speaks」松浦洋介\n"
        "(DOVA-SYNDROME: https://dova-s.jp/bgm/play22758.html)\n"
        "SE: 「オカルト系タイトルコール」Causality Sound\n"
        "(DOVA-SYNDROME: https://dova-s.jp/se/detail/1489)\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "#怪談 #ホラー #朗読 #怖い話 #百鬼朗読"
    ),
    "youtube_tags": "怪談,ホラー,朗読,怖い話,心霊,恐怖",
    "youtube_pinned_comment_template": (
        "ご視聴ありがとうございます！\n"
        "チャンネル登録・高評価していただけると励みになります🙏\n\n"
        "▶ 怪談再生リスト: {playlist_url}\n"
        "他にもたくさんの怪談を朗読しています。ぜひご覧ください！\n\n"
        "怖かったらコメントで教えてください👻"
    ),
    "youtube_channel_name": "",
    "youtube_contact_email": "",
    "youtube_schedule_enabled": True,
    # "weekly" = 毎週指定曜日 (従来動作) / "interval" = N日おき
    "youtube_schedule_mode": "interval",
    "youtube_schedule_interval_days": 2,
    "youtube_schedule_day": "saturday",
    "youtube_schedule_hour": 20,
    "youtube_schedule_minute": 0,
    # ── アップロードの堅牢化 ──
    # 1MB チャンクだと 30 分動画で 2000 リクエストになり、途中で
    # BrokenPipe / ConnectionReset を踏む確率が高い。大きめのチャンクに
    # して回数を減らしつつ、切断時は resumable セッションを再開する。
    "youtube_upload_chunk_mb": 8,
    "youtube_upload_max_retries": 10,
    # ── オートパイロット (起動時に全ワーカーを自動開始) ──
    "autopilot_long_enabled": False,
    "autopilot_short_enabled": False,
    # 自動運転時のブレーキ。未処理ストーリーが1000件以上あるので、これが無いと
    # ワーカーが動画を作り続けて YouTube のクォータを使い切る。
    # 1日あたりのアップロード上限。YouTube Data API は videos.insert 1回で
    # 1600 ユニット消費し、1日の割当が 10000 なので実質 6 本が上限。
    "youtube_daily_upload_limit": 5,
    # 予約投稿がこの日数より先まで埋まっていたら、それ以上アップロードしない。
    # 「1日おき投稿」なら常に 2週間分の在庫を持って自動停止する状態になる。
    "youtube_schedule_max_ahead_days": 14,
    # 24時間あたりの API 費用の上限 (USD)。超えたら生成系を止める。
    # 事前見積もりを外して 5,000 円のクレジットを一晩で使い切ったことがあるので、
    # 見積もりではなく **実測の積み上げ** で止める。0 で無効。
    "api_daily_cost_limit_usd": 3.0,
    # 未投稿の完成動画がこの本数に達したら、音声・画像・動画の生成を止める。
    # アップロード側だけ絞っても上流が回り続けると、未処理ストーリー (1000件超)
    # を延々と動画化してディスクと画像APIを食い潰す。投稿されて在庫が減ったら
    # 自動で再開するので、必要な分だけ作り続ける状態になる。0 で無効。
    "autopilot_max_ready_videos": 10,
    # Shorts settings
    "shorts_leading_silence": 0.0,
    "shorts_trailing_silence": 0.5,
    "shorts_num_scenes": 2,
    "shorts_image_size": "1024x1792",
    "shorts_image_aspect_ratio": "9:16",
    "shorts_max_char_count": 880,
    "shorts_target_char_count": 440,
    "shorts_vhs_enabled": True,
    # ── Shorts 画像の調達方法 ──────────────────────────────────
    # 2枚目以降 (本編シーン) の調達方法。
    # "pool_first" = assets/shorts/scene/ に画像があればそこから決定的に選ぶ。
    #                無ければ AI 生成にフォールバック。
    # "pool"       = プールのみ (空ならエラー)
    # "ai"         = 毎回 AI 生成
    # 1枚目はタイトル文字を焼き込む都合で作品ごとに固有なので、プール対象外。
    "shorts_scene_source": "pool_first",
    # プール画像に VHS ポストエフェクトを掛けるか。手動で差し替えていた
    # 従来運用 (replace_scene_image) は VHS 無しだったので既定 False。
    "shorts_pool_apply_vhs": False,
    # Shorts 専用の画像モデル。空なら image_model を継承。
    # 注意: imagen-* 系は Gemini Developer API キーでは動かない
    # (generate_images は Vertex AI 専用。"This method is only supported in
    # Gemini Enterprise Agent Platform mode" で失敗する)。
    # gemini-2.5-flash-image は日本語が崩れる (赤い土 → 我い土)。
    # 3.1-flash-image はタイトル文字を正しく描けるので、これを既定にする。
    "shorts_image_model": "gemini-3.1-flash-image",
    # サムネイルのバッジはカテゴリ名をそのまま出す。長すぎる等の理由で
    # 別の文言にしたいカテゴリだけここで上書きする。
    # カテゴリ未設定の作品はバッジ自体を出さない。
    "shorts_badge_overrides": {"合掌コンクール応募作品": "ホラホリ図書館"},
    # 1枚目 (サムネイル) のプロンプト。画像AIにタイトル文字ごと描かせる。
    # {title} = 動画タイトル, {subject} = 本文から起こした「何を写すか」の一文,
    # {badge_instruction} = カテゴリバッジの指示 (未設定なら「出すな」に変わる)。
    "shorts_thumbnail_prompt": (
        "YouTube thumbnail for a Japanese kaidan (ghost story) narration channel.\n\n"
        "CONCEPT: Authentic Japanese horror. Quiet dread and an uncanny presence — "
        "not cheap horror, not zombies, not splatter. Cinematic film-still quality, "
        "realistic textures, natural human anatomy and perspective. Dark tonality, "
        "strong shadows, deep blacks, dark blues and greys, with blood-red used only "
        "as an accent.\n\n"
        "SUBJECT: {subject}\n"
        "Place this subject large, in the centre-to-right of the frame. Keep the "
        "background dark and uncluttered so the subject reads instantly. Simple, "
        "strong composition.\n\n"
        "TEXT (must be rendered in the image, in correct Japanese):\n"
        "{badge_instruction}"
        "- Left side, large and vertical: the video title, reading exactly: {title}\n"
        "- Typography: heavy Japanese brush calligraphy, aged washi paper feel, "
        "horror-movie title styling. White and red on black, with blood spatter, "
        "cracks and worn texture.\n"
        "- The Japanese characters must be correct, clean and unbroken. No invented "
        "glyphs.\n\n"
        "FRAMING: the photographic scene must fill the ENTIRE frame, edge to edge and "
        "corner to corner. Absolutely no black bars, no letterboxing, no borders, no "
        "empty margins and no flat blank areas. The bottom of the frame must still "
        "show the scene (floor, ground, shadow), just without any important subject "
        "or text in it.\n\n"
        "SAFE AREA (the YouTube Shorts interface overlays the frame): compose so that "
        "no face, no key subject and no text falls within the right 15% or the bottom "
        "25% of the frame. Those regions must still contain scenery — they are simply "
        "kept free of anything important. Never crop a face or a character at the "
        "frame edge.\n\n"
        "DO NOT INCLUDE: any logo, watermark, app icon, play button, social media "
        "mark, UI element, subscribe button, timestamp, signature or brand mark of "
        "any kind. No YouTube or Shorts iconography. No English text.\n\n"
        "QUALITY: high-quality cinematic visual, readable at thumbnail size, striking "
        "enough to stop a scroll. Aspect ratio 9:16, vertical."
    ),
    "shorts_speed": 1.15,
    "shorts_scrape_delay": 2.0,
    "shorts_endscreen_duration": 5.0,
    "shorts_bgm_volume": 0.1,
    "shorts_youtube_title_template": "【怪談朗読】{title}｜怖い話 #Shorts",
    "shorts_youtube_description_template": (
        "【怪談朗読】{title}\n\n"
        "百鬼朗読へようこそ。\n"
        "日本各地に伝わる怪談・不思議な話・人怖を朗読でお届けします。\n"
        "チャンネル登録・高評価よろしくお願いします。\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "引用元: 怖い話投稿サイト 奇々怪々\n"
        "「{title}」{url}\n"
        "作者: {author}\n\n"
        "音声: VOICEVOX:{speaker}\n"
        "BGM: 「Where the Light Never Speaks」松浦洋介\n"
        "(DOVA-SYNDROME: https://dova-s.jp/bgm/play22758.html)\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "#怪談 #ホラー #朗読 #怖い話 #Shorts #百鬼朗読"
    ),
    # HHS-sourced stories migrated to Shorts pipeline use this template instead
    # (HHS 規約で正しい引用元表記が必要なため)。`#ホラホリ` タグも付与。
    "shorts_hhs_youtube_description_template": (
        "【怪談朗読】{title} #Shorts\n\n"
        "百鬼朗読へようこそ。\n"
        "日本各地に伝わる怪談・不思議な話・人怖を朗読でお届けします。\n"
        "チャンネル登録・高評価よろしくお願いします。\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "引用元:HHS図書館より\n"
        "「{title}」{url}\n\n"
        "音声: VOICEVOX:{speaker}\n"
        "BGM: 「Where the Light Never Speaks」松浦洋介\n"
        "(DOVA-SYNDROME: https://dova-s.jp/bgm/play22758.html)\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "#怪談 #ホラー #朗読 #怖い話 #Shorts #ホラホリ #百鬼朗読"
    ),
    # Shorts のタイトル・説明文をどこから作るか。
    #   "template" = 下のテンプレートをそのまま使う (チャンネルの定型フォーマット)
    #   "llm"      = Gemini に釣りタイトルを書かせる
    # LLM 版はテンプレートの体裁 (百鬼朗読の挨拶・BGMクレジット・タグ構成) を
    # 一切踏襲しないため、既定はテンプレート。
    "shorts_metadata_source": "template",
    # Shorts 特化タグ: 睡眠用/作業用BGM 等の長尺向けは除外。
    # ホラホリは HHS由来 Short のみ upload 時に追加（source依存）。
    "shorts_youtube_tags": (
        "怪談,ホラー,朗読,怖い話,心霊,恐怖,怪談朗読,実話怪談,都市伝説,"
        "百鬼朗読,VOICEVOX,怖い話朗読,人怖,不思議な話,Shorts"
    ),
    # TikTok Inbox/Draft アップロード後にユーザーがコピペする用のキャプション。
    # TikTok仕様上 Inbox は動画ファイル本体のみ送信。説明文/ハッシュタグは
    # ユーザーがTikTokアプリ内で貼り付けるためのテキスト。
    "tiktok_caption_template": (
        "【怪談朗読】{title}\n\n"
        "引用元: 怖い話投稿サイト 奇々怪々\n"
        "「{title}」 by {author}\n\n"
        "音声: VOICEVOX:{speaker}\n"
        "BGM: 「Where the Light Never Speaks」松浦洋介 (DOVA-SYNDROME)\n\n"
        "#怪談 #ホラー #朗読 #怖い話 #ショート怪談 #百鬼朗読 #実話怪談 #都市伝説"
    ),
    # HHS由来の Shorts は引用元表記が異なる + ホラホリタグ必須。
    "tiktok_hhs_caption_template": (
        "【怪談朗読】{title}\n\n"
        "引用元: HHS図書館より\n"
        "「{title}」\n\n"
        "音声: VOICEVOX:{speaker}\n"
        "BGM: 「Where the Light Never Speaks」松浦洋介 (DOVA-SYNDROME)\n\n"
        "#怪談 #ホラー #朗読 #怖い話 #ショート怪談 #ホラホリ #百鬼朗読"
    ),
    # Narration dictionary customization (user additions on top of hardcoded defaults)
    "reading_overrides": {},      # surface(漢字等) → ひらがな読み
    "compound_replacements": {},  # 置換元 → 置換後（MeCab前に適用）
    "keep_as_kanji": [],          # 漢字のまま残す語のリスト
    # AI proofread prompt (Gemini で MeCab 後段の読み修正に使用)。{raw} と {processed}
    # はそれぞれ原文(漢字混じり)と機械変換結果に置換される。
    "ai_proofread_prompt": (
        "次の【原文】(漢字かな混じり)と【読み原稿】(原文をMeCab形態素解析で機械的に"
        "ひらがな化したもの)を照合してください。読み原稿には、MeCabが漢字を取り違えて"
        "誤った読みにした箇所が混ざっています(例: 鏡→「きょう」が正しくは「かがみ」、"
        "片仮名→「かたかめい」が正しくは「かたかな」、掛布団→「かけふだん」が正しくは"
        "「かけぶとん」)。\n\n"
        "あなたの仕事は、読み原稿のうち【原文の読みと食い違う箇所だけ】を、原文の"
        "正しい読み(ひらがな)に直して全文を返すことです。誤読はひらがなになっている"
        "ことが多いので、ひらがな箇所でも必要なら直してください。\n\n"
        "## 厳守事項\n"
        "1. 言い換え・要約・語句の追加削除は禁止。読みの訂正だけ。文字数・改行はほぼ不変。\n"
        "2. 助詞の「わ」(←は)「え」(←へ)は既に正しい。元に戻さない。\n"
        "3. 濁点・半濁点は必ず保持。「が ぎ ぐ げ ご ざ じ ず ぜ ぞ だ ぢ づ で ど "
        "ば び ぶ べ ぼ ぱ ぴ ぷ ぺ ぽ」等の濁音・半濁音を「か さ た は わ」等の清音に"
        "絶対に変えない。\n"
        "4. カタカナ・数字・アルファベット・記号・改行はそのまま。\n"
        "5. 直す箇所が無ければ入力を一字一句そのまま返す。説明・前置き・コードブロックは"
        "付けず、本文のみ返す。\n"
        "6. 下記【漢字のまま残す語】に挙がっている語は、意図的に漢字のまま残して"
        "あります。誤変換ではないのでひらがなに直さず、漢字のまま出力してください。\n\n"
        "{keep_kanji}"
        "## 出力に絶対含めてはいけない文字\n"
        "- 結合用濁点 U+3099, 結合用半濁点 U+309A\n"
        "- 標準濁点 U+309B, 標準半濁点 U+309C, 半角濁点 U+FF9E, 半角半濁点 U+FF9F\n"
        "- 異体字・互換漢字 (U+2E80–U+2EFF, U+F900–U+FAFF 等)\n"
        "ば は 1文字 U+3070、ぱ は U+3071。分解形は禁止。\n\n"
        "参照用の原文(漢字混じり):\n"
        "---\n"
        "{raw}\n"
        "---\n\n"
        "機械変換結果(これを必要箇所だけ修正):\n"
        "---\n"
        "{processed}\n"
        "---"
    ),
}


def load_config() -> dict:
    """Load config from TOML file, falling back to defaults."""
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    if CONFIG_PATH.exists():
        with open(CONFIG_PATH, "rb") as f:
            user_config = tomllib.load(f)
        return {**_DEFAULTS, **user_config}
    return dict(_DEFAULTS)


def _toml_quote(s: str) -> str:
    """Escape a string for inclusion in a TOML basic string."""
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def save_config(config: dict) -> None:
    """Save config to TOML file."""
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    lines = []
    for key, value in config.items():
        if isinstance(value, str):
            if "\n" in value:
                lines.append(f'{key} = """\n{value}"""')
            else:
                lines.append(f'{key} = "{value}"')
        elif isinstance(value, bool):
            lines.append(f"{key} = {'true' if value else 'false'}")
        elif isinstance(value, float):
            lines.append(f"{key} = {value}")
        elif isinstance(value, int):
            lines.append(f"{key} = {value}")
        elif isinstance(value, list):
            items = ", ".join(_toml_quote(str(v)) for v in value)
            lines.append(f"{key} = [{items}]")
        elif isinstance(value, dict):
            items = ", ".join(
                f"{_toml_quote(str(k))} = {_toml_quote(str(v))}"
                for k, v in value.items()
            )
            lines.append(f"{key} = {{{items}}}")
        else:
            lines.append(f"{key} = {value!r}")
    CONFIG_PATH.write_text("\n".join(lines) + "\n")


def get(key: str):
    """Get a single config value."""
    return load_config().get(key, _DEFAULTS.get(key))
