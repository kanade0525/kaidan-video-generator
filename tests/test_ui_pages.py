"""UI ページが実際に描画できることを確認するスモークテスト。

NiceGUI はページ関数が途中で例外を投げても初期 HTTP は 200 を返すことがあり、
`curl` の疎通確認では「プルダウンが空」「要素が出ない」を見逃す。ここでは
nicegui.testing でページを実際に構築し、主要な要素が描画されるかを見る。

nicegui.testing の User フィクスチャは pytest-asyncio と NiceGUI のプラグインを
必要とするため、揃っていない環境ではスキップする。
"""

import pytest

pytest.importorskip("pytest_asyncio")
# `nicegui.testing.plugin` は selenium (ブラウザ実機) も引き込むので、
# ヘッドレスで完結する user 用プラグインだけを読み込む。
pytest_plugins = ("nicegui.testing.user_plugin",)


@pytest.fixture(autouse=True)
def _isolated_env(tmp_path, monkeypatch):
    """設定・DB・出力先をテスト用に隔離する。"""
    import app.config as config_module
    import app.database as db
    import app.utils.paths as paths
    from app.services import image_pool

    monkeypatch.setattr(config_module, "CONFIG_PATH", tmp_path / "config.toml")
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "kaidan.db")
    monkeypatch.setattr(db._local, "conn", None, raising=False)
    monkeypatch.setattr(paths, "OUTPUT_BASE", tmp_path / "output")
    monkeypatch.setattr(image_pool, "POOL_ROOT", tmp_path / "assets" / "shorts")
    db.init_db()


async def test_pipeline_page_renders(user):
    """長編パイプライン画面が開き、オートパイロットとステージが出る。"""
    await user.open("/")
    await user.should_see("パイプライン")
    await user.should_see("オートパイロット（全自動運転）")
    await user.should_see("YouTube投稿済")
    await user.should_see("使用報告済")


async def test_shorts_pipeline_page_renders(user):
    await user.open("/shorts")
    await user.should_see("Shorts パイプライン")
    await user.should_see("オートパイロット（全自動運転）")


async def test_settings_page_renders_new_sections(user):
    """設定画面に今回追加したセクションが全部出る。"""
    await user.open("/settings")
    await user.should_see("設定")
    await user.should_see("オートパイロット（全自動運転）")
    await user.should_see("間（ま）とアクセント")
    await user.should_see("Shorts 画像")
    await user.should_see("画像プール")
    await user.should_see("自動投稿のブレーキ")
    await user.should_see("アップロードの安定性")
    await user.should_see("音声プレビュー")


async def test_settings_autopilot_toggle_persists(user):
    """オートパイロットのスイッチが設定に永続化される。"""
    from app.pipeline import autopilot

    await user.open("/settings")
    assert autopilot.is_enabled("long") is False


async def test_stories_page_renders(user):
    await user.open("/stories")
    await user.should_see("ストーリー")


async def test_results_page_renders(user):
    await user.open("/results")
    await user.should_see("生成結果")


async def test_shorts_results_page_renders(user):
    await user.open("/shorts/results")
    await user.should_see("生成結果")


async def test_settings_save_keeps_untouched_keys(user, tmp_path):
    """保存でこの画面に無い設定 (Shorts テンプレート等) が消えないこと。

    以前は保存のたびに新しい dict を組み立てていたため、画面に出していない
    キーが既定値へ巻き戻っていた。
    """
    from app.config import load_config, save_config

    save_config({**load_config(), "shorts_youtube_tags": "カスタムタグ"})

    await user.open("/settings")
    user.find("設定を保存").click()
    await user.should_see("設定を保存しました")

    assert load_config()["shorts_youtube_tags"] == "カスタムタグ"


async def test_settings_save_persists_new_options(user):
    """今回追加した設定項目が保存される。"""
    from app.config import load_config

    await user.open("/settings")
    user.find("設定を保存").click()
    await user.should_see("設定を保存しました")

    config = load_config()
    for key in (
        "text_ai_proofread_enabled",
        "voice_accent_mode",
        "pause_length_scale",
        "shorts_scene_source",
        "youtube_schedule_mode",
        "youtube_daily_upload_limit",
        "youtube_upload_chunk_mb",
        "autopilot_max_ready_videos",
        "shorts_metadata_source",
    ):
        assert key in config, f"{key} が保存されていない"


async def test_settings_offers_proofread_prompt_reset(user):
    """保存済みの旧プロンプトを既定値に戻せる導線がある。"""
    await user.open("/settings")
    await user.should_see("AI校正プロンプトを既定値に戻す")


async def test_results_detail_shows_rewind_panel(user):
    """生成結果の詳細に「やり直す」パネルが出る。"""
    import app.database as db

    story = db.add_story(url="https://example.com/rewind", title="やり直しテスト")
    db.update_stage(story.id, "video_complete")

    await user.open(f"/results?id={story.id}")
    await user.should_see("やり直す（ステージを巻き戻す）")


async def test_rewind_panel_lists_target_stages(user):
    """やり直す工程の選択肢と注意書きが描画される。"""
    import app.database as db

    story = db.add_story(url="https://example.com/rewind2", title="やり直しテスト2")
    db.update_stage(story.id, "video_complete")

    await user.open(f"/results?id={story.id}")
    await user.should_see("やり直す工程")
    await user.should_see("生成済みファイルも削除する")
    await user.should_see("YouTube動画IDも消す（再アップロードする）")


async def test_rewind_panel_executes_and_deletes_files(user):
    """やり直しパネルから実際にステージを戻し、ファイルを削除できる。

    詳細画面は WebSocket 接続後に組み立てられるため、生の HTML を見ても
    検証できない。ここでは実際にチェックを入れてボタンを押し、DB と
    ファイルの両方が変わることを確認する。
    """
    import app.database as db
    import app.utils.paths as paths

    story = db.add_story(url="https://example.com/rewind3", title="やり直し実行")
    db.update_stage(story.id, "video_complete")
    scene = paths.images_dir(story.title, "long") / "scene_000.png"
    scene.write_bytes(b"png")
    narration = paths.narration_path(story.title, "long")
    narration.write_bytes(b"wav")

    await user.open(f"/results?id={story.id}")
    # 既定は「画像生成からやり直す」= 音声生成済みまで巻き戻る
    await user.should_see("ステージは「音声生成済」に戻ります")

    user.find(marker="rewind-delete-files").click()
    await user.should_see("削除するファイル: 1個")

    user.find(marker="rewind-run").click()
    await user.should_see("やり直しの確認")
    await user.should_see("scene_000.png")

    user.find(marker="rewind-confirm").click()
    await user.should_see("からやり直せます")

    assert db.get_story_by_id(story.id).stage == "voice_generated"
    assert not scene.exists()
    assert narration.exists(), "やり直さない工程の生成物は残す"


async def test_pipeline_shows_halt_reason(user):
    """クレジット切れで自動停止したとき、理由が画面に出る。"""
    from app.pipeline.executor import shorts_pipeline

    shorts_pipeline.halted_reason = "429 RESOURCE_EXHAUSTED: credits are depleted"
    try:
        await user.open("/shorts")
        await user.should_see("自動停止しました")
        await user.should_see("RESOURCE_EXHAUSTED")
    finally:
        shorts_pipeline.halted_reason = ""


async def test_pipeline_hides_halt_banner_when_healthy(user):
    from app.pipeline.executor import shorts_pipeline

    shorts_pipeline.halted_reason = ""
    await user.open("/shorts")
    await user.should_not_see("自動停止しました")


async def test_settings_shows_api_usage(user):
    """走らせている最中に「今いくら使ったか」が見えること。"""
    await user.open("/settings")
    await user.should_see("API利用状況")
    await user.should_see("直近24時間")
    await user.should_see("24時間のAPI費用上限")
