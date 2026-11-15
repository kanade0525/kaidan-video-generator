"""動画生成の無駄な再エンコードと色範囲の混在を防ぐテスト。

背景: Short 1本 (約60秒) の生成で全編の再エンコードが4回走っていた。
  1. スライドショー: 静止画を毎秒25回読み直していた
  2. 字幕の重ね合わせ
  3. バナー・クレジットの描画 (2 と別パスだった)
  4. 終了画面との連結: 形式不一致でコピーできず再エンコードになっていた
さらに、タイトル画像が JPEG 由来だとタイトルだけフルレンジになり、連結後の
再エンコードで本編の黒が浮いていた (暗部が白っぽくなる)。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.utils import ffmpeg


@pytest.fixture
def calls(monkeypatch):
    """run_ffmpeg を記録だけするものに差し替える。"""
    recorded: list[list[str]] = []

    def fake(args, timeout=600):
        recorded.append(list(args))

    monkeypatch.setattr(ffmpeg, "run_ffmpeg", fake)
    monkeypatch.setattr(ffmpeg, "get_audio_duration", lambda p: 60.0)
    return recorded


def _encode_calls(calls):
    return [c for c in calls if "libx264" in c]


class TestStillImageLoop:
    """静止画は1回だけ読み込み、フィルタで同じフレームを繰り返す。

    `-loop 1 -i image` は同じ画像を毎フレーム読み直すので使わない。
    """

    def _assert_reads_image_once(self, enc):
        assert "-loop" not in enc, "画像を毎フレーム読み直さない"
        vf = enc[enc.index("-vf") + 1]
        assert vf == ffmpeg.STILL_LOOP_FILTER
        assert vf.startswith("format=yuv420p,"), "色変換も繰り返す前に1回だけ"
        assert "loop=loop=-1:size=1" in vf
        assert "scale" not in vf and "crop" not in vf, "スケールを毎フレーム掛け直さない"
        # 入力を出力と同じ fps で読む = fps 変換が入らずフレーム数が正確
        assert enc[enc.index("-framerate") + 1] == enc[enc.index("-r") + 1] == "30"
        assert enc.index("-framerate") < enc.index("-i"), "入力オプションなので -i より前"

    def test_single_image_is_read_once(self, calls, tmp_path):
        img = tmp_path / "scene.png"
        img.write_bytes(b"png")
        ffmpeg.create_slideshow([img], tmp_path / "a.wav", tmp_path / "out.mp4",
                                target_width=1080, target_height=1920)
        (enc,) = _encode_calls(calls)
        self._assert_reads_image_once(enc)

    def test_multi_image_prescales_once_per_image(self, calls, tmp_path):
        imgs = [tmp_path / f"scene_{i}.png" for i in range(3)]
        for p in imgs:
            p.write_bytes(b"png")
        ffmpeg.create_slideshow(imgs, tmp_path / "a.wav", tmp_path / "out.mp4",
                                target_width=1080, target_height=1920)
        prescales = [c for c in calls if "-frames:v" in c]
        encodes = _encode_calls(calls)
        assert len(prescales) == 3 and len(encodes) == 3
        for enc in encodes:
            self._assert_reads_image_once(enc)
            assert "_slide_prescaled_" in enc[enc.index("-i") + 1]

    def test_multi_image_without_target_does_not_prescale(self, calls, tmp_path):
        imgs = [tmp_path / f"scene_{i}.png" for i in range(2)]
        for p in imgs:
            p.write_bytes(b"png")
        ffmpeg.create_slideshow(imgs, tmp_path / "a.wav", tmp_path / "out.mp4")
        assert [c for c in calls if "-frames:v" in c] == []
        encodes = _encode_calls(calls)
        assert len(encodes) == 2
        for enc in encodes:
            self._assert_reads_image_once(enc)

    def test_clip_durations_are_passed_exactly(self, calls, tmp_path):
        """端数の尺もそのまま渡す (フレームが欠けるとスライドの切り替えがずれる)。"""
        imgs = [tmp_path / f"scene_{i}.png" for i in range(2)]
        for p in imgs:
            p.write_bytes(b"png")
        ffmpeg.create_slideshow(imgs, tmp_path / "a.wav", tmp_path / "out.mp4",
                                durations=[1.337, 0])
        encodes = _encode_calls(calls)
        assert [enc[enc.index("-t") + 1] for enc in encodes] == ["1.337", "58.663"]


class TestColorRange:
    """全パートを標準レンジ (tv) に揃える。混ざると本編の黒が浮く。"""

    def test_title_clip_is_converted_to_tv_range(self, calls, tmp_path):
        ffmpeg.create_title_clip(tmp_path / "t.png", tmp_path / "t.wav", tmp_path / "t.mp4")
        graph = calls[0][calls[0].index("-filter_complex") + 1]
        assert "out_range=tv" in graph
        assert "format=yuv420p" in graph

    def test_normalize_converts_to_tv_range(self, calls, tmp_path):
        ffmpeg._normalize_video(tmp_path / "in.mp4", tmp_path / "out.ts", width=1080, height=1920)
        vf = calls[0][calls[0].index("-vf") + 1]
        assert "out_range=tv" in vf

    @pytest.mark.parametrize(("color_range", "expected"), [
        ("pc", False),      # フルレンジはコピーで繋がない (再エンコードで変換)
        ("tv", True),
        (None, True),       # 未指定は標準レンジ扱い
    ])
    def test_concat_copy_requires_non_full_range(self, monkeypatch, color_range, expected):
        monkeypatch.setattr(ffmpeg, "_probe_video_params", lambda p: {
            "codec": "h264", "width": 1080, "height": 1920,
            "pix_fmt": "yuv420p", "fps": 30.0, "color_range": color_range,
        })
        assert ffmpeg._video_matches_concat_target(Path("x.mp4"), 1080, 1920, 30) is expected

    def test_full_range_pix_fmt_is_not_copied(self, monkeypatch):
        monkeypatch.setattr(ffmpeg, "_probe_video_params", lambda p: {
            "codec": "h264", "width": 1080, "height": 1920,
            "pix_fmt": "yuvj420p", "fps": 30.0, "color_range": "pc",
        })
        assert ffmpeg._video_matches_concat_target(Path("x.mp4"), 1080, 1920, 30) is False


class TestBurnAllOverlaysSinglePass:
    """字幕・バナー・クレジットは1回のエンコードで焼く。"""

    TRICKY = "O'Brien: 100%, [a];b\\c「怪談」"

    @pytest.fixture
    def burn(self, monkeypatch, tmp_path):
        """burn_all_overlays を呼び、ffmpeg 実行時点の一時ファイルの中身も記録する。"""
        recorded: list[dict] = []

        def fake(args, timeout=600):
            graph = ""
            for key in ("-filter_complex", "-vf"):
                if key in args:
                    graph = args[args.index(key) + 1]
            files = _textfiles_in(graph)
            recorded.append({
                "args": list(args),
                "graph": graph,
                "texts": [Path(f).read_text(encoding="utf-8") for f in files],
                "files": files,
            })

        monkeypatch.setattr(ffmpeg, "run_ffmpeg", fake)
        monkeypatch.setattr(ffmpeg, "get_audio_duration", lambda p: 60.0)
        monkeypatch.setattr(ffmpeg, "generate_scroll_image", lambda **kw: (880, 3000))
        scroll = tmp_path / "scroll_subtitle.txt"
        scroll.write_text("一行目\n二行目", encoding="utf-8")

        def run(**kw):
            ffmpeg.burn_all_overlays(tmp_path / "raw.mp4", tmp_path / "out.mp4", **kw)
            return recorded

        run.scroll = scroll
        return run

    def test_scroll_and_text_overlays_use_one_encode(self, burn):
        recorded = burn(
            scroll_textfile=burn.scroll, scroll_duration=50.0,
            banner_text="ショート怪談", credit_lines=["ホラホリ図書館", "「題名」"],
        )
        assert len(recorded) == 1, "全編の再エンコードは1回だけ"
        graph = recorded[0]["graph"]
        assert "overlay=" in graph and "drawtext=" in graph and "drawbox=" in graph
        args = recorded[0]["args"]
        assert "-filter_complex" in args
        assert args[args.index("-map") + 1] == "[vout]"

    def test_output_format_allows_concat_copy(self, burn):
        """後段の連結でコピーできる形式 (yuv420p) で出す。"""
        recorded = burn(scroll_textfile=burn.scroll, scroll_duration=50.0, banner_text="x")
        args = recorded[0]["args"]
        assert args[args.index("-pix_fmt") + 1] == "yuv420p"
        assert recorded[0]["graph"].rstrip("[vout]").endswith("format=yuv420p")

    def test_text_is_passed_verbatim_through_files(self, burn):
        """記号を含む文字列もそのまま描画される (フィルタ構文と衝突させない)。"""
        recorded = burn(
            scroll_textfile=burn.scroll, scroll_duration=50.0,
            banner_text=self.TRICKY, credit_lines=[self.TRICKY, "二行目"],
        )
        assert recorded[0]["texts"].count(self.TRICKY) == 2
        assert "二行目" in recorded[0]["texts"]
        assert self.TRICKY not in recorded[0]["graph"]
        assert "expansion=none" in recorded[0]["graph"]

    def test_temp_text_files_are_removed(self, burn):
        recorded = burn(scroll_textfile=burn.scroll, scroll_duration=50.0,
                        banner_text="x", credit_lines=["y"])
        assert recorded[0]["files"], "一時ファイル経由で渡している"
        assert not any(Path(f).exists() for f in recorded[0]["files"])

    def test_temp_text_files_are_removed_on_failure(self, monkeypatch, tmp_path):
        seen: list[str] = []

        def boom(args, timeout=600):
            seen.extend(_textfiles_in(args[args.index("-vf") + 1]))
            raise RuntimeError("ffmpeg failed")

        monkeypatch.setattr(ffmpeg, "run_ffmpeg", boom)
        monkeypatch.setattr(ffmpeg, "get_audio_duration", lambda p: 60.0)
        with pytest.raises(RuntimeError):
            ffmpeg.burn_all_overlays(tmp_path / "raw.mp4", tmp_path / "out.mp4", banner_text="x")
        assert seen and not any(Path(f).exists() for f in seen)

    def test_text_only_uses_simple_filter(self, burn):
        recorded = burn(banner_text="ショート怪談", credit_lines=["クレジット"])
        assert len(recorded) == 1
        assert "-vf" in recorded[0]["args"] and "-filter_complex" not in recorded[0]["args"]

    def test_credit_lines_are_stacked_bottom_up(self, burn):
        """行の並びは従来どおり (最後の行が一番下)。"""
        recorded = burn(banner_text=None, mask_zones=False,
                        credit_lines=["上の行", "下の行"],
                        credit_font_size=52, credit_margin_bottom=320)
        graph = recorded[0]["graph"]
        texts = dict(zip(recorded[0]["files"], recorded[0]["texts"], strict=True))
        positions = {}
        for part in graph.split("drawtext=")[1:]:
            f = part.split("textfile='")[1].split("'")[0]
            positions[texts[f]] = int(part.split(":y=h-")[1].split(":")[0].split(",")[0])
        assert positions == {"下の行": 320, "上の行": 320 + 52 + 16}


def _textfiles_in(graph: str) -> list[str]:
    return [seg.split("'")[0] for seg in graph.split("textfile='")[1:]]
