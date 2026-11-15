from __future__ import annotations

from pathlib import Path

from nicegui import ui

from app.config import load_config, save_config


def settings_page():
    """Settings page for all pipeline parameters."""
    ui.label("設定").classes("text-2xl font-bold mb-4")

    config = load_config()

    _api_usage_card()
    _autopilot_card()

    # Voice settings
    with ui.card().classes("w-full mb-4 p-4"):
        ui.label("音声設定").classes("text-lg font-bold mb-2")

        speakers = _get_speakers()
        speaker_options = {s["id"]: s["name"] for s in speakers} if speakers else {}

        speaker_select = ui.select(
            speaker_options, value=config.get("speaker_id", 47), label="話者"
        ).classes("w-64")

        with ui.row().classes("gap-4"):
            speed_slider = ui.slider(min=0.5, max=2.0, step=0.1, value=config.get("speed", 0.9))
            ui.label().bind_text_from(speed_slider, "value", lambda v: f"速度: {v}")

        with ui.row().classes("gap-4"):
            pitch_slider = ui.slider(min=-1.0, max=1.0, step=0.1, value=config.get("pitch", 0.0))
            ui.label().bind_text_from(pitch_slider, "value", lambda v: f"ピッチ: {v}")

        with ui.row().classes("gap-4"):
            intonation_slider = ui.slider(
                min=0.0, max=2.0, step=0.1, value=config.get("intonation", 1.0)
            )
            ui.label().bind_text_from(intonation_slider, "value", lambda v: f"抑揚: {v}")

        with ui.row().classes("gap-4"):
            volume_slider = ui.slider(min=0.0, max=2.0, step=0.1, value=config.get("volume", 1.2))
            ui.label().bind_text_from(volume_slider, "value", lambda v: f"音量: {v}")

        ui.separator().classes("my-3")
        ui.label("間（ま）とアクセント").classes("text-md font-bold mt-2 mb-1")
        ui.label(
            "怪談は間の取り方で怖さが変わります。値を変えたら「変換テスト」下の"
            "試聴で確かめてください。"
        ).classes("text-xs text-gray-500 mb-2")

        with ui.row().classes("gap-4"):
            pre_phoneme = ui.slider(
                min=0.0, max=1.5, step=0.05, value=config.get("pre_phoneme_length", 0.1),
            )
            ui.label().bind_text_from(
                pre_phoneme, "value", lambda v: f"発話前の無音: {v}s",
            )

        with ui.row().classes("gap-4"):
            post_phoneme = ui.slider(
                min=0.0, max=1.5, step=0.05, value=config.get("post_phoneme_length", 0.1),
            )
            ui.label().bind_text_from(
                post_phoneme, "value", lambda v: f"発話後の無音: {v}s",
            )

        with ui.row().classes("gap-4"):
            pause_scale = ui.slider(
                min=0.5, max=3.0, step=0.1, value=config.get("pause_length_scale", 1.0),
            )
            ui.label().bind_text_from(
                pause_scale, "value", lambda v: f"句読点ポーズ倍率: {v}",
            )

        with ui.row().classes("gap-4 items-end flex-wrap"):
            newline_mode = ui.select(
                {
                    "period": "。として読む（長い間）",
                    "comma": "、として読む（短い間）",
                    "none": "区切らず続けて読む",
                },
                value=config.get("voice_newline_mode", "period"),
                label="改行の扱い",
            ).classes("w-64")
            accent_mode = ui.select(
                {
                    "verified_kanji": "漢字混じり原稿を検証して使う（自然）",
                    "hiragana": "ひらがな原稿のみ（従来）",
                },
                value=config.get("voice_accent_mode", "verified_kanji"),
                label="アクセント",
            ).classes("w-80")
        ui.label(
            "「漢字混じり原稿」は、辞書で読みを直した語だけをかなにして残りは漢字の"
            "まま VOICEVOX に渡す方式です。ひらがなだけだと語の切れ目が分からず"
            "アクセントが平板になるため。読みが1文字でも変わるチャンクは自動で"
            "ひらがな原稿に戻すので、読み間違いが増えることはありません。"
        ).classes("text-xs text-gray-500")

    # Image settings
    with ui.card().classes("w-full mb-4 p-4"):
        ui.label("画像設定").classes("text-lg font-bold mb-2")

        image_model = ui.input("モデル", value=config.get("image_model", "gemini-2.5-flash-image")).classes("w-64")

        with ui.row().classes("gap-4 items-end"):
            image_size = ui.select(
                ["1792x1024", "1024x1024", "1280x720", "1024x576"],
                value=config.get("image_size", "1792x1024"),
                label="サイズ (AirForce用)",
            ).classes("w-48")
            image_aspect = ui.select(
                ["16:9", "4:3", "1:1", "3:4", "9:16"],
                value=config.get("image_aspect_ratio", "16:9"),
                label="アスペクト比 (Gemini/Imagen用)",
            ).classes("w-48")

        num_scenes = ui.number("シーン数", value=config.get("num_scenes", 3), min=1, max=10)
        image_style = ui.textarea(
            "スタイルプロンプト", value=config.get("image_style", "")
        ).classes("w-full")
        image_negative = ui.textarea(
            "ネガティブプロンプト", value=config.get("image_negative_prompt", "")
        ).classes("w-full")

        ui.label("Imagen専用パラメータ").classes("text-md font-bold mt-4 mb-2")
        ui.label("以下はimagenモデル使用時のみ有効です").classes("text-xs text-gray-500 mb-2")

        with ui.row().classes("gap-4 items-end flex-wrap"):
            image_person = ui.select(
                ["DONT_ALLOW", "ALLOW_ADULT", "ALLOW_ALL"],
                value=config.get("image_person_generation", "DONT_ALLOW"),
                label="人物生成",
            ).classes("w-48")
            image_mime = ui.select(
                ["image/png", "image/jpeg"],
                value=config.get("image_output_mime", "image/png"),
                label="出力フォーマット",
            ).classes("w-48")

        with ui.row().classes("gap-4"):
            image_compression = ui.slider(min=10, max=100, step=5, value=config.get("image_compression_quality", 90))
            ui.label().bind_text_from(image_compression, "value", lambda v: f"圧縮品質: {int(v)}")

        with ui.row().classes("gap-4"):
            image_guidance = ui.slider(min=1.0, max=20.0, step=0.5, value=config.get("image_guidance_scale", 7.0))
            ui.label().bind_text_from(image_guidance, "value", lambda v: f"ガイダンススケール: {v}")

        with ui.row().classes("gap-4 items-end"):
            image_seed = ui.number("シード (0=ランダム)", value=config.get("image_seed", 0), min=0)
            image_enhance = ui.checkbox("プロンプト自動改善", value=config.get("image_enhance_prompt", False))
            image_watermark = ui.checkbox("透かし追加", value=config.get("image_add_watermark", False))

        image_rate = ui.number(
            "API間隔(秒)", value=config.get("image_rate_limit", 15), min=1, max=120
        )

    # Video settings
    with ui.card().classes("w-full mb-4 p-4"):
        ui.label("動画設定").classes("text-lg font-bold mb-2")

        fps = ui.number("FPS", value=config.get("fps", 30), min=1, max=60)

        with ui.row().classes("gap-4"):
            fade_in = ui.slider(min=0.0, max=5.0, step=0.5, value=config.get("fade_in", 1.0))
            ui.label().bind_text_from(fade_in, "value", lambda v: f"フェードイン: {v}s")

        with ui.row().classes("gap-4"):
            fade_out = ui.slider(min=0.0, max=5.0, step=0.5, value=config.get("fade_out", 1.0))
            ui.label().bind_text_from(fade_out, "value", lambda v: f"フェードアウト: {v}s")

        # BGM selection
        bgm_files = _get_bgm_files()
        bgm_options = {"": "なし", **{str(f): f.name for f in bgm_files}}
        bgm_select = ui.select(
            bgm_options, value=config.get("bgm_path", ""), label="BGM"
        ).classes("w-64")

        with ui.row().classes("gap-4"):
            bgm_volume = ui.slider(
                min=0.0, max=1.0, step=0.05, value=config.get("bgm_volume", 0.1)
            )
            ui.label().bind_text_from(bgm_volume, "value", lambda v: f"BGM音量: {v}")

        # OP/ED selection
        op_files = _get_video_files("op")
        op_options = {"": "なし", **{str(f): f.name for f in op_files}}
        op_select = ui.select(
            op_options, value=config.get("op_path", ""), label="OP動画"
        ).classes("w-64")

        with ui.row().classes("gap-4"):
            op_fade = ui.slider(min=0.0, max=3.0, step=0.5, value=config.get("op_fade_out", 1.0))
            ui.label().bind_text_from(op_fade, "value", lambda v: f"OPフェードアウト: {v}s")

        ed_files = _get_video_files("ed")
        ed_options = {"": "なし", **{str(f): f.name for f in ed_files}}
        ed_select = ui.select(
            ed_options, value=config.get("ed_path", ""), label="ED動画"
        ).classes("w-64")

        # Bundle (詰め合わせ動画) settings
        ui.separator().classes("my-2")
        ui.label("詰め合わせ動画 (1〜2時間の長編)").classes("text-md font-semibold mt-2")
        ui.label(
            "Long作品を OP+ジングル区切り+ED で繋いだ長編動画。各ストーリー間の"
            "切替音 (ジングル) を assets/jingle/ に置いて選択してください。",
        ).classes("text-xs text-gray-500")
        jingle_files = _get_jingle_files()
        jingle_options = {"": "なし (無音0.5秒)", **{str(f): f.name for f in jingle_files}}
        jingle_select = ui.select(
            jingle_options, value=config.get("bundle_jingle_path", ""), label="ジングル",
        ).classes("w-64")

    # Shorts image settings
    with ui.card().classes("w-full mb-4 p-4"):
        ui.label("Shorts 画像").classes("text-lg font-bold mb-2")
        ui.label(
            "1枚目（サムネイル）はタイトル文字ごと画像AIに描かせるため、作品ごとに"
            "固有です。2枚目以降（本編シーン）は気に入った画像をプールに入れておけば、"
            "毎回AIに描かせずに済みます。"
        ).classes("text-xs text-gray-500 mb-2")

        with ui.row().classes("gap-4 items-end flex-wrap"):
            shorts_scene_source = ui.select(
                {
                    "pool_first": "プール優先（空ならAI生成）",
                    "pool": "プールのみ",
                    "ai": "毎回AI生成",
                },
                value=config.get("shorts_scene_source", "pool_first"),
                label="2枚目以降（シーン）",
            ).classes("w-64")
            shorts_img_model = ui.input(
                "Shorts画像モデル（空=共通設定を使う）",
                value=config.get("shorts_image_model", ""),
            ).classes("w-80")
        ui.label(
            "日本語のタイトルを絵に描かせるので、モデルは日本語が崩れないものを"
            "選んでください（gemini-2.5-flash-image は崩れます）。"
        ).classes("text-xs text-gray-500")

        shorts_thumb_prompt = ui.textarea(
            "サムネイル生成プロンプト（{title}=タイトル, {subject}=本文から起こした主役）",
            value=config.get("shorts_thumbnail_prompt", ""),
        ).classes("w-full").props("autogrow")

        shorts_pool_vhs = ui.checkbox(
            "プール画像にもVHS加工をかける",
            value=config.get("shorts_pool_apply_vhs", False),
        )

        ui.separator().classes("my-3")
        ui.label("Shorts のタイトル・説明文").classes("font-bold")
        shorts_meta_source = ui.select(
            {
                "template": "テンプレートどおり（チャンネルの定型フォーマット）",
                "llm": "Geminiに釣りタイトルを書かせる",
            },
            value=config.get("shorts_metadata_source", "template"),
            label="生成方法",
        ).classes("w-96")
        ui.label(
            "「Geminiに書かせる」を選ぶと 😱【怖い話】… のような釣りタイトルに"
            "なりますが、挨拶・タグ構成はテンプレートと変わります"
            "（引用元とBGMクレジットはどちらでも必ず付きます）。"
        ).classes("text-xs text-gray-500")

        ui.separator().classes("my-3")
        _image_pool_editor()

    # Text settings
    with ui.card().classes("w-full mb-4 p-4"):
        ui.label("テキスト設定").classes("text-lg font-bold mb-2")

        text_model_input = ui.input(
            "テキスト処理モデル", value=config.get("text_model", "gpt-4o-mini")
        ).classes("w-64")
        gemini_model = ui.input(
            "Geminiモデル (画像プロンプト用)", value=config.get("gemini_model", "gemini-2.5-flash-lite")
        ).classes("w-64")
        max_chunk = ui.number("最大チャンク長", value=config.get("max_chunk", 200), min=50, max=1000)
        text_prompt = ui.textarea(
            "テキスト処理プロンプト", value=config.get("text_prompt", "")
        ).classes("w-full")

        auto_proofread = ui.checkbox(
            "テキスト処理時にAI校正を自動で適用する（確定ボタン不要）",
            value=config.get("text_ai_proofread_enabled", True),
        )
        ui.label(
            "ONにすると、パイプラインのテキスト処理が校正済みのテキストを直接"
            "出力します。生成結果ページの「AI校正プレビュー」で差分を確認してから"
            "適用する手動フローもそのまま使えます。"
        ).classes("text-xs text-gray-500 mb-2")

        ai_proofread_prompt = ui.textarea(
            "AI校正プロンプト (Gemini, MeCab後段)",
            value=config.get("ai_proofread_prompt", ""),
        ).classes("w-full").props("autogrow")
        ui.label(
            "MeCab結果と原文が {processed} と {raw} に展開されて Gemini に渡されます。"
            "{keep_kanji} には「漢字のまま残す語」のうち本文に登場するものが"
            "差し込まれ、AIがそれをひらがなに直すのを防ぎます。"
            "{raw} か {processed} が欠けるとAI校正は無効化されます。"
        ).classes("text-xs text-gray-500")

        proofread_hint = ui.label("").classes("text-xs text-amber-600")

        def _update_proofread_hint():
            if "{keep_kanji}" in (ai_proofread_prompt.value or ""):
                proofread_hint.text = ""
            else:
                proofread_hint.text = (
                    "このプロンプトには {keep_kanji} がありません。"
                    "「漢字のまま残す語」の指示は先頭に自動で足されるので動作しますが、"
                    "既定値に戻すと本来の位置に入ります。"
                )

        def _reset_proofread_prompt():
            from app.config import _DEFAULTS

            ai_proofread_prompt.set_value(_DEFAULTS["ai_proofread_prompt"])
            _update_proofread_hint()
            ui.notify(
                "既定のプロンプトに戻しました。「設定を保存」で確定します。",
                color="positive",
            )

        ui.button(
            "AI校正プロンプトを既定値に戻す", on_click=_reset_proofread_prompt,
        ).props("size=sm flat")
        ai_proofread_prompt.on_value_change(lambda _: _update_proofread_hint())
        _update_proofread_hint()

    # Narration dictionary settings
    with ui.card().classes("w-full mb-4 p-4"):
        ui.label("朗読辞書").classes("text-lg font-bold mb-2")
        ui.label(
            "VOICEVOX朗読用のカスタム辞書。ハードコードされたデフォルトに追加・上書きする形で適用されます。"
        ).classes("text-xs text-gray-500 mb-2")

        reading_overrides_state: dict[str, str] = dict(config.get("reading_overrides", {}))
        compound_replacements_state: dict[str, str] = dict(config.get("compound_replacements", {}))
        keep_as_kanji_state: list[str] = list(config.get("keep_as_kanji", []))

        # Reading overrides
        ui.label("① 読み上書き (語 → ひらがな読み)").classes("font-bold mt-2")
        ui.label("MeCabの読み誤りを修正。例: 所々 → ところどころ").classes("text-xs text-gray-500")
        reading_rows = ui.column().classes("gap-1")

        def refresh_reading():
            reading_rows.clear()
            with reading_rows:
                for surface, reading in list(reading_overrides_state.items()):
                    with ui.row().classes("gap-2 items-center"):
                        ui.label(f"{surface} → {reading}").classes("w-80 font-mono text-sm")
                        ui.button(
                            "削除",
                            on_click=lambda s=surface: (
                                reading_overrides_state.pop(s, None),
                                refresh_reading(),
                            ),
                            color="red",
                        ).props("size=sm flat")

        with ui.row().classes("gap-2 items-end"):
            new_reading_surface = ui.input("語").classes("w-32")
            new_reading_value = ui.input("ひらがな読み").classes("w-32")

            def add_reading():
                s = (new_reading_surface.value or "").strip()
                r = (new_reading_value.value or "").strip()
                if s and r:
                    reading_overrides_state[s] = r
                    new_reading_surface.set_value("")
                    new_reading_value.set_value("")
                    refresh_reading()

            ui.button("追加", on_click=add_reading, color="green").props("size=sm")
        refresh_reading()

        # Compound replacements
        ui.separator().classes("my-3")
        ui.label("② 複合語置換 (置換元 → 置換後)").classes("font-bold mt-2")
        ui.label(
            "MeCab分割で誤読される複合語をMeCab前に置換。例: お父さん → おとうさん"
        ).classes("text-xs text-gray-500")
        compound_rows = ui.column().classes("gap-1")

        def refresh_compound():
            compound_rows.clear()
            with compound_rows:
                for src, dst in list(compound_replacements_state.items()):
                    with ui.row().classes("gap-2 items-center"):
                        ui.label(f"{src} → {dst}").classes("w-80 font-mono text-sm")
                        ui.button(
                            "削除",
                            on_click=lambda s=src: (
                                compound_replacements_state.pop(s, None),
                                refresh_compound(),
                            ),
                            color="red",
                        ).props("size=sm flat")

        with ui.row().classes("gap-2 items-end"):
            new_compound_src = ui.input("置換元").classes("w-32")
            new_compound_dst = ui.input("置換後").classes("w-32")

            def add_compound():
                s = (new_compound_src.value or "").strip()
                d = (new_compound_dst.value or "").strip()
                if s and d:
                    compound_replacements_state[s] = d
                    new_compound_src.set_value("")
                    new_compound_dst.set_value("")
                    refresh_compound()

            ui.button("追加", on_click=add_compound, color="green").props("size=sm")
        refresh_compound()

        # Keep as kanji
        ui.separator().classes("my-3")
        ui.label("③ 漢字のまま残す語").classes("font-bold mt-2")
        ui.label(
            "VOICEVOXがひらがなだと誤読する語。例: 母 (はは→ワワ と読まれるため漢字のまま)"
        ).classes("text-xs text-gray-500")
        keep_rows = ui.column().classes("gap-1")

        def refresh_keep():
            keep_rows.clear()
            with keep_rows:
                for word in list(keep_as_kanji_state):
                    with ui.row().classes("gap-2 items-center"):
                        ui.label(word).classes("w-80 font-mono text-sm")
                        ui.button(
                            "削除",
                            on_click=lambda w=word: (
                                keep_as_kanji_state.remove(w),
                                refresh_keep(),
                            ),
                            color="red",
                        ).props("size=sm flat")

        with ui.row().classes("gap-2 items-end"):
            new_keep_word = ui.input("漢字のまま残す語").classes("w-32")

            def add_keep():
                w = (new_keep_word.value or "").strip()
                if w and w not in keep_as_kanji_state:
                    keep_as_kanji_state.append(w)
                    new_keep_word.set_value("")
                    refresh_keep()

            ui.button("追加", on_click=add_keep, color="green").props("size=sm")
        refresh_keep()

        # Preview
        ui.separator().classes("my-3")
        ui.label("変換テスト").classes("font-bold mt-2")
        ui.label(
            "現在の辞書内容で変換結果を確認します（保存してから実行）。"
        ).classes("text-xs text-gray-500")
        preview_input = ui.textarea(
            "テスト文章", value="お父さんは3人で母を見た。"
        ).classes("w-full")
        preview_output = ui.label("").classes(
            "w-full p-2 bg-gray-100 font-mono whitespace-pre-wrap"
        )

        preview_source = ui.label("").classes(
            "w-full p-2 bg-gray-100 font-mono whitespace-pre-wrap",
        )
        preview_accent = ui.label("").classes(
            "w-full p-2 bg-gray-50 font-mono text-xs whitespace-pre-wrap",
        )

        def run_preview():
            from app.services.text_processor import (
                _mecab_to_hiragana,
                analyze_accent,
                build_narration_source,
            )

            current = load_config()
            current["reading_overrides"] = dict(reading_overrides_state)
            current["compound_replacements"] = dict(compound_replacements_state)
            current["keep_as_kanji"] = list(keep_as_kanji_state)
            save_config(current)

            text = preview_input.value or ""
            result = _mecab_to_hiragana(text) or "(変換失敗)"
            preview_output.set_text(f"ひらがな原稿: {result}")

            mixed = build_narration_source(text)
            preview_source.set_text(
                f"漢字混じり原稿（VOICEVOXへ渡す方）: {mixed or '(生成失敗)'}",
            )

            # 辞書に登録した語のアクセント型を出す。「読みは合っているのに
            # イントネーションが変」の切り分けに使う。
            words = list(keep_as_kanji_state) + list(reading_overrides_state)
            lines = []
            for w in words[:20]:
                info = analyze_accent(w)
                if info:
                    reading, accent = info
                    kind = "平板型" if accent == 0 else f"{accent}型"
                    lines.append(f"{w}: {reading} / {kind}")
                else:
                    lines.append(f"{w}: (辞書に単一語として登録されていない)")
            preview_accent.set_text(
                "辞書語のアクセント:\n" + ("\n".join(lines) if lines else "(辞書が空です)"),
            )
            ui.notify("辞書を保存してプレビュー実行", color="positive")

        ui.button("保存して変換テスト", on_click=run_preview, color="blue").props("size=sm")

        _voice_preview_player()

    # YouTube settings
    with ui.card().classes("w-full mb-4 p-4"):
        ui.label("YouTube設定").classes("text-lg font-bold mb-2")

        from app.services import youtube_uploader

        # Auth status
        with ui.row().classes("gap-4 items-center mb-2"):
            if youtube_uploader.is_authenticated():
                ui.label("認証済み").classes("text-green-500 font-bold")
            elif youtube_uploader.is_configured():
                ui.label("未認証（client_secret.json あり）").classes("text-yellow-500")
            else:
                ui.label("未設定（data/client_secret.json を配置してください）").classes("text-red-500")

            def do_auth():
                # Open OAuth flow in a new tab. The server will redirect
                # to Google's consent page and handle the callback.
                ui.run_javascript('window.open("/youtube/auth", "_blank")')
                ui.notify(
                    "新しいタブで認証を完了してください。"
                    "完了後、このページをリロードしてください。",
                    color="info",
                )

            ui.button("YouTube認証", on_click=do_auth, color="red").props("size=sm")

        yt_category = ui.select(
            {"24": "エンターテインメント", "22": "ブログ", "27": "教育", "1": "映画", "10": "音楽"},
            value=config.get("youtube_category_id", "24"),
            label="カテゴリ",
        ).classes("w-48")

        yt_privacy = ui.select(
            {"private": "非公開", "unlisted": "限定公開", "public": "公開"},
            value=config.get("youtube_privacy_status", "private"),
            label="公開状態",
        ).classes("w-48")

        yt_title_tmpl = ui.input(
            "タイトルテンプレート（{title}で怪談名に置換）",
            value=config.get("youtube_title_template", "【怪談朗読】{title}"),
        ).classes("w-full")

        yt_description = ui.textarea(
            "説明テンプレート（{title}でタイトルに置換）",
            value=config.get("youtube_description_template", ""),
        ).classes("w-full")

        yt_tags = ui.input(
            "タグ（カンマ区切り）",
            value=config.get("youtube_tags", "怪談,ホラー,朗読"),
        ).classes("w-full")

        ui.label("使用報告（ホラホリ利用規約で必須）").classes("text-md font-bold mt-4 mb-2")
        yt_channel = ui.input(
            "チャンネル名", value=config.get("youtube_channel_name", "")
        ).classes("w-64")
        yt_email = ui.input(
            "連絡先メールアドレス", value=config.get("youtube_contact_email", "")
        ).classes("w-64")
        ui.label("アップロード成功時にHHS図書館に使用報告を自動送信します").classes("text-xs text-gray-500")

        ui.label("予約投稿").classes("text-md font-bold mt-4 mb-2")
        yt_schedule_enabled = ui.checkbox(
            "予約投稿を有効にする", value=config.get("youtube_schedule_enabled", True)
        )
        with ui.row().classes("gap-4 items-end flex-wrap"):
            yt_schedule_mode = ui.select(
                {"interval": "N日おき", "weekly": "毎週この曜日"},
                value=config.get("youtube_schedule_mode", "interval"),
                label="スケジュール",
            ).classes("w-40")
            yt_interval_days = ui.number(
                "間隔（日）", value=config.get("youtube_schedule_interval_days", 2),
                min=1, max=30,
            ).classes("w-28")
            yt_schedule_day = ui.select(
                {
                    "monday": "月曜", "tuesday": "火曜", "wednesday": "水曜",
                    "thursday": "木曜", "friday": "金曜", "saturday": "土曜", "sunday": "日曜",
                },
                value=config.get("youtube_schedule_day", "saturday"),
                label="曜日",
            ).classes("w-32")
            yt_schedule_hour = ui.number(
                "時", value=config.get("youtube_schedule_hour", 20), min=0, max=23
            ).classes("w-20")
            yt_schedule_minute = ui.number(
                "分", value=config.get("youtube_schedule_minute", 0), min=0, max=59
            ).classes("w-20")

        # 曜日は weekly のときだけ意味があるので、モードに応じて出し分ける
        yt_schedule_day.bind_visibility_from(
            yt_schedule_mode, "value", lambda v: v == "weekly",
        )
        yt_interval_days.bind_visibility_from(
            yt_schedule_mode, "value", lambda v: v == "interval",
        )

        next_slot_label = ui.label("").classes("text-xs text-gray-500")

        def _refresh_next_slot():
            from app.services import youtube_uploader
            try:
                slot = youtube_uploader.resolve_publish_time()
            except Exception as e:  # noqa: BLE001
                next_slot_label.text = f"次の公開枠を計算できません: {e}"
                return
            if slot:
                next_slot_label.text = (
                    f"次にアップロードする動画の公開予定: {slot[:16]}（日本時間）"
                )
            else:
                next_slot_label.text = "予約投稿は無効です（アップロード後すぐ公開状態になります）"

        _refresh_next_slot()
        ui.button("次の公開枠を再計算", on_click=_refresh_next_slot).props("size=sm flat")

        ui.label("自動投稿のブレーキ").classes("text-md font-bold mt-4 mb-2")
        ui.label(
            "オートパイロット時に投稿しすぎないための上限です。YouTube API は"
            "1日あたり実質6本までしかアップロードできません。"
        ).classes("text-xs text-gray-500 mb-1")
        with ui.row().classes("gap-4 items-end flex-wrap"):
            yt_daily_limit = ui.number(
                "1日の最大アップロード数", value=config.get("youtube_daily_upload_limit", 5),
                min=0, max=6,
            ).classes("w-48")
            yt_max_ahead = ui.number(
                "予約の先読み日数", value=config.get("youtube_schedule_max_ahead_days", 14),
                min=1, max=180,
            ).classes("w-40")
            max_ready_videos = ui.number(
                "未投稿の完成動画の上限", value=config.get("autopilot_max_ready_videos", 10),
                min=0, max=200,
            ).classes("w-48")
            api_cost_limit = ui.number(
                "24時間のAPI費用上限 (USD)",
                value=config.get("api_daily_cost_limit_usd", 3.0),
                min=0, max=100, step=0.5, format="%.2f",
            ).classes("w-56")
        ui.label(
            "予約がこの日数より先まで埋まったらアップロードを止めます"
            "（在庫を持ちすぎない）。未投稿の完成動画が上限に達したら、"
            "音声・画像・動画の生成そのものを止めます（0で無効）。"
            "投稿されて在庫が減れば自動で再開します。"
        ).classes("text-xs text-gray-500")

        ui.label("アップロードの安定性").classes("text-md font-bold mt-4 mb-2")
        with ui.row().classes("gap-4 items-end flex-wrap"):
            yt_chunk_mb = ui.number(
                "チャンクサイズ(MB)", value=config.get("youtube_upload_chunk_mb", 8),
                min=1, max=64,
            ).classes("w-40")
            yt_upload_retries = ui.number(
                "切断時の再開回数", value=config.get("youtube_upload_max_retries", 10),
                min=0, max=50,
            ).classes("w-40")
        ui.label(
            "Broken pipe などで切れても、同じアップロードセッションを途中から"
            "再開します。チャンクを大きくするほど通信回数が減り切断しにくくなります。"
        ).classes("text-xs text-gray-500")

    # Save button
    def save():
        # 既存の設定を土台にして上書きする。ゼロから組み立てると、この画面に
        # 出していないキー (Shorts のテンプレート等) が既定値へ巻き戻る。
        new_config = load_config()
        new_config.update({
            "speaker_id": speaker_select.value,
            "speed": speed_slider.value,
            "pitch": pitch_slider.value,
            "intonation": intonation_slider.value,
            "volume": volume_slider.value,
            "image_model": image_model.value,
            "image_size": image_size.value,
            "image_aspect_ratio": image_aspect.value,
            "image_person_generation": image_person.value,
            "image_output_mime": image_mime.value,
            "image_compression_quality": int(image_compression.value),
            "image_negative_prompt": image_negative.value,
            "image_guidance_scale": image_guidance.value,
            "image_seed": int(image_seed.value),
            "image_enhance_prompt": image_enhance.value,
            "image_add_watermark": image_watermark.value,
            "num_scenes": int(num_scenes.value),
            "image_style": image_style.value,
            "image_rate_limit": int(image_rate.value),
            "fps": int(fps.value),
            "fade_in": fade_in.value,
            "fade_out": fade_out.value,
            "bgm_path": bgm_select.value,
            "bgm_volume": bgm_volume.value,
            "op_path": op_select.value,
            "op_fade_out": op_fade.value,
            "ed_path": ed_select.value,
            "bundle_jingle_path": jingle_select.value,
            "text_model": text_model_input.value,
            "gemini_model": gemini_model.value,
            "max_chunk": int(max_chunk.value),
            "text_prompt": text_prompt.value,
            "ai_proofread_prompt": ai_proofread_prompt.value,
            "youtube_title_template": yt_title_tmpl.value,
            "youtube_category_id": yt_category.value,
            "youtube_privacy_status": yt_privacy.value,
            "youtube_description_template": yt_description.value,
            "youtube_tags": yt_tags.value,
            "youtube_channel_name": yt_channel.value,
            "youtube_contact_email": yt_email.value,
            "youtube_schedule_enabled": yt_schedule_enabled.value,
            "youtube_schedule_day": yt_schedule_day.value,
            "youtube_schedule_hour": int(yt_schedule_hour.value),
            "youtube_schedule_minute": int(yt_schedule_minute.value),
            "reading_overrides": dict(reading_overrides_state),
            "compound_replacements": dict(compound_replacements_state),
            "keep_as_kanji": list(keep_as_kanji_state),
            # 音声の間・アクセント
            "pre_phoneme_length": pre_phoneme.value,
            "post_phoneme_length": post_phoneme.value,
            "pause_length_scale": pause_scale.value,
            "voice_newline_mode": newline_mode.value,
            "voice_accent_mode": accent_mode.value,
            # テキスト
            "text_ai_proofread_enabled": auto_proofread.value,
            # Shorts 画像
            "shorts_scene_source": shorts_scene_source.value,
            "shorts_image_model": shorts_img_model.value,
            "shorts_thumbnail_prompt": shorts_thumb_prompt.value,
            "shorts_pool_apply_vhs": shorts_pool_vhs.value,
            "shorts_metadata_source": shorts_meta_source.value,
            # YouTube 予約・自動投稿
            "youtube_schedule_mode": yt_schedule_mode.value,
            "youtube_schedule_interval_days": int(yt_interval_days.value),
            "youtube_daily_upload_limit": int(yt_daily_limit.value),
            "youtube_schedule_max_ahead_days": int(yt_max_ahead.value),
            "autopilot_max_ready_videos": int(max_ready_videos.value),
            "api_daily_cost_limit_usd": float(api_cost_limit.value),
            "youtube_upload_chunk_mb": int(yt_chunk_mb.value),
            "youtube_upload_max_retries": int(yt_upload_retries.value),
        })
        save_config(new_config)
        ui.notify("設定を保存しました", color="positive")

    ui.button("設定を保存", on_click=save, color="green").classes("mt-4")


def _get_speakers() -> list[dict]:
    """Fetch speakers from VOICEVOX, return empty list on failure."""
    try:
        from app.services.voice_generator import get_speakers
        result = []
        for s in get_speakers():
            for style in s.get("styles", []):
                result.append({"id": style["id"], "name": f"{s['name']} ({style['name']})"})
        return result
    except Exception:
        return []


def _get_bgm_files() -> list[Path]:
    """List available BGM files."""
    for d in [Path("assets/bgm"), Path("bgm")]:
        if d.exists():
            return sorted(d.glob("*.*"))
    return []


def _get_video_files(subdir: str) -> list[Path]:
    """List available video files in assets subdirectory."""
    d = Path(f"assets/{subdir}")
    if not d.exists():
        return []
    return sorted(d.glob("*.mp4"))


def _get_jingle_files() -> list[Path]:
    """List available jingle files (audio or short video clips)."""
    d = Path("assets/jingle")
    if not d.exists():
        return []
    files: list[Path] = []
    for ext in ("*.mp3", "*.wav", "*.m4a", "*.mp4"):
        files.extend(d.glob(ext))
    return sorted(f for f in files if not f.name.startswith("."))


def _autopilot_card() -> None:
    """オートパイロット (全自動運転) の操作カード。

    有効にするとワーカーが常駐し、スクレイピングから YouTube 投稿・使用報告まで
    人手なしで流れる。アプリ再起動後も設定が残り、自動で走り出す。
    """
    from app.pipeline import autopilot

    with ui.card().classes("w-full mb-4 p-4"):
        ui.label("オートパイロット（全自動運転）").classes("text-lg font-bold mb-1")
        ui.label(
            "ONにすると、未処理のストーリーを自動で最後まで処理して YouTube に"
            "予約投稿します。投稿しすぎないよう「1日の最大アップロード数」と"
            "「予約の先読み日数」でブレーキがかかります（YouTube設定内）。"
        ).classes("text-xs text-gray-500 mb-3")

        status_labels: dict[str, ui.label] = {}

        def _refresh_status():
            for ct, label in status_labels.items():
                running = autopilot.is_running(ct)
                label.text = "稼働中" if running else "停止中"
                label.classes(
                    replace="text-sm font-bold "
                    + ("text-green-600" if running else "text-gray-500"),
                )

        def _make_toggle(ct: str, title: str):
            with ui.row().classes("items-center gap-3"):
                switch = ui.switch(title, value=autopilot.is_enabled(ct))
                status_labels[ct] = ui.label("").classes("text-sm")

            def _on_change(e, ct=ct):
                try:
                    autopilot.set_enabled(ct, bool(e.value))
                except Exception as exc:  # noqa: BLE001
                    ui.notify(f"切り替えに失敗: {exc}", color="negative")
                    return
                _refresh_status()
                ui.notify(
                    f"{title}: {'開始' if e.value else '停止'}しました",
                    color="positive" if e.value else "warning",
                )

            switch.on_value_change(_on_change)

        _make_toggle("long", "長編パイプラインを自動運転")
        _make_toggle("short", "Shorts パイプラインを自動運転")
        _refresh_status()

        def _safe_refresh():
            try:
                _refresh_status()
            except RuntimeError:
                status_timer.active = False

        status_timer = ui.timer(5.0, _safe_refresh)


def _image_pool_editor() -> None:
    """Shorts 画像プールのアップロード・一覧・削除 UI。"""
    from app.services import image_pool

    ui.label("画像プール").classes("font-bold")
    ui.label(
        "縦長（1080x1920 相当）の画像を入れてください。はみ出す分は中央基準で"
        "切り取られます。同じストーリーには常に同じ画像が選ばれます。"
    ).classes("text-xs text-gray-500 mb-2")

    for kind, title in ((image_pool.SCENE, "2枚目以降（本編シーン）"),):
        with ui.expansion(title, icon="image").classes("w-full"):
            grid = ui.row().classes("gap-2 flex-wrap w-full")

            def _refresh(kind=kind, grid=grid):
                grid.clear()
                files = image_pool.list_pool(kind)
                with grid:
                    if not files:
                        ui.label(
                            "まだ画像がありません（この状態だと毎回AI生成になります）",
                        ).classes("text-sm text-gray-500")
                        return
                    for f in files:
                        with ui.column().classes("items-center gap-1"):
                            ui.image(f).classes("w-24 h-40 object-cover rounded")
                            ui.label(f.name).classes("text-xs w-24 truncate text-center")
                            ui.button(
                                "削除",
                                on_click=lambda n=f.name, k=kind, r=_refresh: (
                                    image_pool.delete_from_pool(k, n),
                                    r(),
                                    ui.notify(f"{n} を削除しました", color="warning"),
                                ),
                                color="red",
                            ).props("size=sm flat dense")

            def _on_upload(e, kind=kind, refresh=_refresh):
                data = e.content.read()
                image_pool.save_to_pool(kind, e.name, data)
                refresh()
                ui.notify(f"{e.name} を追加しました", color="positive")

            ui.upload(
                on_upload=_on_upload, auto_upload=True, multiple=True, label="画像を追加",
            ).props("accept=image/*").classes("w-full")
            _refresh()


def _voice_preview_player() -> None:
    """設定した声・間・アクセントで短い文を試聴する。

    間やアクセントの設定は耳で確かめないと当たりが分からないので、
    設定ページから直接鳴らせるようにしている。
    """
    import threading

    from nicegui import app as nicegui_app

    ui.separator().classes("my-3")
    ui.label("音声プレビュー").classes("font-bold mt-2")
    ui.label(
        "現在保存されている音声設定（速度・間・アクセント）で読み上げます。"
        "設定を変えたら「設定を保存」してから実行してください。"
    ).classes("text-xs text-gray-500")

    preview_text = ui.input(
        "読み上げる文",
        value="夜中、廊下の奥から足音が近づいてきた。",
    ).classes("w-full")

    with ui.row().classes("items-center gap-2"):
        play_btn = ui.button("生成して試聴", color="blue").props("size=sm icon=volume_up")
        spinner = ui.spinner(size="sm")
        spinner.visible = False
        status = ui.label("").classes("text-sm text-gray-500")
    audio_slot = ui.column().classes("w-full")

    state: dict = {"running": False, "done": False, "error": None, "path": None}

    def _work(text: str):
        try:
            from app.services.text_processor import build_narration_source
            from app.services.voice_generator import text_to_speech

            out_dir = Path("data/preview")
            out_dir.mkdir(parents=True, exist_ok=True)
            out = out_dir / "voice_preview.wav"
            out.write_bytes(
                text_to_speech(text, accent_source=build_narration_source(text)),
            )
            state["path"] = out
        except Exception as e:  # noqa: BLE001 — UI へ理由を出したい
            state["error"] = str(e)
        state["done"] = True

    def _start():
        text = (preview_text.value or "").strip()
        if not text:
            ui.notify("読み上げる文を入力してください", color="warning")
            return
        state.update(running=True, done=False, error=None, path=None)
        audio_slot.clear()
        spinner.visible = True
        play_btn.disable()
        status.text = "VOICEVOX で生成中..."
        status.classes(replace="text-sm text-blue-500")
        threading.Thread(target=_work, args=(text,), daemon=True).start()
        timer.active = True

    def _poll():
        if not state["running"]:
            timer.active = False
            return
        try:
            if not state["done"]:
                return
            state["running"] = False
            spinner.visible = False
            play_btn.enable()
            timer.active = False
            if state["error"]:
                status.text = f"生成に失敗: {state['error'][:160]}"
                status.classes(replace="text-sm text-red-500")
                return
            status.text = "生成しました"
            status.classes(replace="text-sm text-green-600")
            path = state["path"]
            nicegui_app.add_static_files("/preview-audio", str(path.parent))
            audio_slot.clear()
            with audio_slot:
                # mtime をクエリに付けてブラウザキャッシュを避ける
                ui.audio(
                    f"/preview-audio/{path.name}?t={int(path.stat().st_mtime)}",
                ).classes("w-full")
        except RuntimeError:
            state["running"] = False
            timer.active = False

    timer = ui.timer(0.5, _poll, active=False)
    play_btn.on_click(_start)


def _api_usage_card() -> None:
    """API の利用実績と概算費用。

    課金が事後にしか分からず、思考トークンの見落としで 5,000 円を一晩で
    使い切ったことがある。走らせている最中に「今いくら使ったか」が
    見えるようにしておく。
    """
    from datetime import datetime, timedelta, timezone

    from app import database as db
    from app.config import get as cfg_get

    with ui.card().classes("w-full mb-4 p-4"):
        ui.label("API利用状況").classes("text-lg font-bold mb-1")
        ui.label(
            "実測のトークン数から概算した費用です。思考モデル(gemini-2.5-pro等)は"
            "思考トークンも出力として課金されます。"
        ).classes("text-xs text-gray-500 mb-2")

        total_label = ui.label("").classes("text-xl font-bold")
        table_box = ui.column().classes("w-full gap-1")

        def _refresh():
            since = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
            spent = db.get_api_cost_since(since)
            limit = float(cfg_get("api_daily_cost_limit_usd") or 0)
            yen = spent * 150  # 概算 (1USD=150円)
            text = f"直近24時間: ${spent:.3f} (約{yen:.0f}円)"
            if limit > 0:
                text += f" / 上限 ${limit:.2f}"
            total_label.text = text
            over = limit > 0 and spent >= limit
            total_label.classes(
                replace="text-xl font-bold "
                + ("text-red-600" if over else "text-gray-800"),
            )

            table_box.clear()
            rows = db.get_api_usage_summary(since)
            with table_box:
                if not rows:
                    ui.label("直近24時間の利用はありません").classes(
                        "text-sm text-gray-500",
                    )
                    return
                ui.table(
                    columns=[
                        {"name": "model", "label": "モデル", "field": "model",
                         "align": "left"},
                        {"name": "purpose", "label": "用途", "field": "purpose",
                         "align": "left"},
                        {"name": "calls", "label": "回数", "field": "calls"},
                        {"name": "input_tokens", "label": "入力", "field": "input_tokens"},
                        {"name": "output_tokens", "label": "出力", "field": "output_tokens"},
                        {"name": "thinking_tokens", "label": "うち思考",
                         "field": "thinking_tokens"},
                        {"name": "images", "label": "画像", "field": "images"},
                        {"name": "cost", "label": "費用(USD)", "field": "cost"},
                    ],
                    rows=[
                        {**r, "cost": f"${r['cost_usd']:.3f}"} for r in rows
                    ],
                    row_key="model",
                ).classes("w-full").props("dense flat")

        _refresh()
        ui.button("更新", on_click=_refresh).props("size=sm flat icon=refresh")

        def _safe():
            try:
                _refresh()
            except RuntimeError:
                usage_timer.active = False

        usage_timer = ui.timer(30.0, _safe)
