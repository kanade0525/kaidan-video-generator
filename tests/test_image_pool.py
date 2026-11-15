"""Shorts 画像プールのテスト。

1枚目 (タイトルカード背景) の AI 生成品質が安定せず毎回手で差し替えていた
問題への対処として、キュレーション済み画像から決定的に選ぶ仕組みを入れた。
"""

from io import BytesIO

import pytest
from PIL import Image

import app.config as config_module
from app.services import image_pool


@pytest.fixture
def pool_root(tmp_path, monkeypatch):
    monkeypatch.setattr(image_pool, "POOL_ROOT", tmp_path / "shorts")
    return tmp_path / "shorts"


@pytest.fixture
def user_config(tmp_path, monkeypatch):
    config_path = tmp_path / "config.toml"
    monkeypatch.setattr(config_module, "CONFIG_PATH", config_path)

    def _set(**values):
        config_module.save_config(values)

    return _set


def _png(width=100, height=200, color=(20, 20, 30)) -> bytes:
    buf = BytesIO()
    Image.new("RGB", (width, height), color).save(buf, format="PNG")
    return buf.getvalue()


class TestPoolListing:
    def test_empty_when_missing(self, pool_root):
        assert image_pool.list_pool(image_pool.SCENE) == []

    def test_lists_images_sorted(self, pool_root):
        image_pool.save_to_pool(image_pool.SCENE, "b.png", _png())
        image_pool.save_to_pool(image_pool.SCENE, "a.png", _png())
        names = [p.name for p in image_pool.list_pool(image_pool.SCENE)]
        assert names == ["a.png", "b.png"]

    def test_ignores_non_images_and_hidden(self, pool_root):
        d = image_pool.pool_dir(image_pool.SCENE)
        (d / "notes.txt").write_text("x")
        (d / ".DS_Store").write_bytes(b"x")
        image_pool.save_to_pool(image_pool.SCENE, "ok.png", _png())
        assert [p.name for p in image_pool.list_pool(image_pool.SCENE)] == ["ok.png"]


class TestPick:
    def test_none_when_empty(self, pool_root):
        assert image_pool.pick(image_pool.SCENE, "タイトル") is None

    def test_always_returns_a_pool_file(self, pool_root):
        for i in range(5):
            image_pool.save_to_pool(image_pool.SCENE, f"{i}.png", _png())
        names = {p.name for p in image_pool.list_pool(image_pool.SCENE)}
        for _ in range(20):
            assert image_pool.pick(image_pool.SCENE, "怪談タイトル").name in names

    def test_selection_is_random_not_fixed_per_title(self, pool_root):
        """同じタイトルでも毎回同じ画像にならない。

        以前はタイトルのハッシュで決定的に選んでいたため、作品数がプール枚数を
        超えると同じ画像が規則的に繰り返されていた。
        """
        for i in range(20):
            image_pool.save_to_pool(image_pool.SCENE, f"{i}.png", _png())
        picked = {image_pool.pick(image_pool.SCENE, "同じタイトル").name for _ in range(40)}
        assert len(picked) > 1

    def test_spreads_across_the_whole_pool(self, pool_root):
        for i in range(8):
            image_pool.save_to_pool(image_pool.SCENE, f"{i}.png", _png())
        picked = {image_pool.pick(image_pool.SCENE, f"タイトル{i}").name for i in range(200)}
        assert len(picked) == 8


class TestSaveAndDelete:
    def test_avoids_overwriting_same_name(self, pool_root):
        a = image_pool.save_to_pool(image_pool.SCENE, "x.png", _png())
        b = image_pool.save_to_pool(image_pool.SCENE, "x.png", _png())
        assert a != b
        assert len(image_pool.list_pool(image_pool.SCENE)) == 2

    def test_normalises_unknown_extension(self, pool_root):
        saved = image_pool.save_to_pool(image_pool.SCENE, "x.bmp", _png())
        assert saved.suffix == ".png"

    def test_strips_directory_components(self, pool_root):
        """パス区切りを含む名前でプール外に書き出させない。"""
        saved = image_pool.save_to_pool(image_pool.SCENE, "../../evil.png", _png())
        assert saved.parent == image_pool.pool_dir(image_pool.SCENE)

    def test_delete_removes_file(self, pool_root):
        saved = image_pool.save_to_pool(image_pool.SCENE, "x.png", _png())
        assert image_pool.delete_from_pool(image_pool.SCENE, saved.name) is True
        assert image_pool.list_pool(image_pool.SCENE) == []

    def test_delete_rejects_paths_outside_pool(self, pool_root, tmp_path):
        outside = tmp_path / "secret.png"
        outside.write_bytes(_png())
        assert image_pool.delete_from_pool(image_pool.SCENE, "../secret.png") is False
        assert outside.exists()


class TestLoadFitted:
    def test_resizes_to_target(self, pool_root):
        saved = image_pool.save_to_pool(image_pool.SCENE, "x.png", _png(400, 400))
        data = image_pool.load_fitted(saved, 1080, 1920)
        img = Image.open(BytesIO(data))
        assert img.size == (1080, 1920)

    def test_converts_to_rgb(self, pool_root):
        buf = BytesIO()
        Image.new("RGBA", (50, 50), (10, 10, 10, 255)).save(buf, format="PNG")
        saved = image_pool.save_to_pool(image_pool.SCENE, "a.png", buf.getvalue())
        img = Image.open(BytesIO(image_pool.load_fitted(saved, 200, 300)))
        assert img.mode == "RGB"


class TestResolveShortsImage:
    """プール/AI の切り替えロジック。"""

    def _resolve(self, source, **kw):
        from app.services.image_generator import _resolve_shorts_image

        defaults = dict(
            kind=image_pool.SCENE,
            source=source,
            key="タイトル",
            offset=0,
            prompt="a dark corridor",
            width=100,
            height=200,
            aspect_ratio="9:16",
            style_suffix=None,
            use_vhs=False,
            candidates=1,
        )
        defaults.update(kw)
        return _resolve_shorts_image(**defaults)

    def test_pool_used_when_available(self, pool_root, user_config, monkeypatch):
        user_config(shorts_pool_apply_vhs=False)
        image_pool.save_to_pool(image_pool.SCENE, "x.png", _png())
        called = []
        monkeypatch.setattr(
            "app.services.image_generator.generate_best_image",
            lambda *a, **k: called.append(1) or b"ai",
        )
        data, from_pool = self._resolve("pool_first")
        assert from_pool is True
        assert called == []
        assert data is not None

    def test_falls_back_to_ai_when_pool_empty(self, pool_root, user_config, monkeypatch):
        user_config()
        monkeypatch.setattr(
            "app.services.image_generator.generate_best_image",
            lambda *a, **k: _png(),
        )
        data, from_pool = self._resolve("pool_first")
        assert from_pool is False
        assert data is not None

    def test_pool_only_raises_when_empty(self, pool_root, user_config, monkeypatch):
        """"pool" 指定でプールが空なら AI を呼ばずに失敗させる。

        黙って黒背景を返すと、真っ黒な動画がそのまま投稿されてしまう。
        """
        from app.services.image_generator import ImageUnavailableError

        user_config()
        monkeypatch.setattr(
            "app.services.image_generator.generate_best_image",
            lambda *a, **k: pytest.fail("AI should not be called"),
        )
        with pytest.raises(ImageUnavailableError, match="プールが空"):
            self._resolve("pool")

    def test_ai_mode_ignores_pool(self, pool_root, user_config, monkeypatch):
        user_config()
        image_pool.save_to_pool(image_pool.SCENE, "x.png", _png())
        monkeypatch.setattr(
            "app.services.image_generator.generate_best_image",
            lambda *a, **k: _png(color=(200, 0, 0)),
        )
        _, from_pool = self._resolve("ai")
        assert from_pool is False

    def test_ai_failure_raises_with_model_name(self, pool_root, user_config, monkeypatch):
        """生成に失敗したら止める。どのモデルで失敗したかもメッセージに出す。"""
        from app.services.image_generator import ImageUnavailableError

        user_config(shorts_image_model="gemini-2.5-flash-image")

        def _boom(*a, **k):
            raise RuntimeError("api down")

        monkeypatch.setattr(
            "app.services.image_generator.generate_best_image", _boom,
        )
        with pytest.raises(ImageUnavailableError) as exc:
            self._resolve("ai")
        assert "gemini-2.5-flash-image" in str(exc.value)
        assert "api down" in str(exc.value)

    def test_broken_pool_image_falls_back_to_ai(self, pool_root, user_config, monkeypatch):
        user_config()
        d = image_pool.pool_dir(image_pool.SCENE)
        (d / "broken.png").write_bytes(b"not an image")
        monkeypatch.setattr(
            "app.services.image_generator.generate_best_image",
            lambda *a, **k: _png(),
        )
        data, from_pool = self._resolve("pool_first")
        assert from_pool is False
        assert data is not None


class TestNeedsAi:
    def test_pool_never_needs_ai(self, pool_root):
        from app.services.image_generator import _needs_ai

        assert _needs_ai("pool", image_pool.SCENE) is False

    def test_pool_first_needs_ai_only_when_empty(self, pool_root):
        from app.services.image_generator import _needs_ai

        assert _needs_ai("pool_first", image_pool.SCENE) is True
        image_pool.save_to_pool(image_pool.SCENE, "x.png", _png())
        assert _needs_ai("pool_first", image_pool.SCENE) is False

    def test_ai_always_needs_ai(self, pool_root):
        from app.services.image_generator import _needs_ai

        assert _needs_ai("ai", image_pool.SCENE) is True


class TestImageScoring:
    def test_flat_image_scores_low(self):
        from app.services.image_generator import score_image_quality

        assert score_image_quality(_png(200, 200, (0, 0, 0))) < 0.2

    def test_detailed_image_scores_higher_than_flat(self):
        import numpy as np

        from app.services.image_generator import score_image_quality

        rng = np.random.default_rng(0)
        arr = rng.integers(40, 140, size=(200, 200, 3), dtype=np.uint8)
        buf = BytesIO()
        Image.fromarray(arr).save(buf, format="PNG")
        noisy = score_image_quality(buf.getvalue())
        flat = score_image_quality(_png(200, 200, (90, 90, 90)))
        assert noisy > flat

    def test_invalid_bytes_score_zero(self):
        from app.services.image_generator import score_image_quality

        assert score_image_quality(b"garbage") == 0.0


class TestGenerateBestImage:
    def test_single_candidate_calls_once(self, monkeypatch):
        from app.services import image_generator

        calls = []
        monkeypatch.setattr(
            image_generator, "generate_image_ai",
            lambda *a, **k: calls.append(1) or _png(),
        )
        image_generator.generate_best_image("p", candidates=1)
        assert len(calls) == 1

    def test_picks_highest_scoring_candidate(self, monkeypatch):
        from app.services import image_generator

        good = _png(64, 64, (100, 100, 100))
        outputs = [_png(64, 64, (0, 0, 0)), good]
        monkeypatch.setattr(
            image_generator, "generate_image_ai", lambda *a, **k: outputs.pop(0),
        )
        monkeypatch.setattr(
            image_generator, "score_image_quality",
            lambda data: 0.9 if data == good else 0.1,
        )
        assert image_generator.generate_best_image("p", candidates=2) == good

    def test_ignores_failed_candidates(self, monkeypatch):
        from app.services import image_generator

        ok = _png()
        results = [RuntimeError("boom"), ok]

        def _gen(*a, **k):
            item = results.pop(0)
            if isinstance(item, Exception):
                raise item
            return item

        monkeypatch.setattr(image_generator, "generate_image_ai", _gen)
        assert image_generator.generate_best_image("p", candidates=2) == ok

    def test_raises_when_all_candidates_fail(self, monkeypatch):
        from app.services import image_generator

        def _boom(*a, **k):
            raise RuntimeError("api down")

        monkeypatch.setattr(image_generator, "generate_image_ai", _boom)
        with pytest.raises(RuntimeError):
            image_generator.generate_best_image("p", candidates=2)


class TestNoSilentFallback:
    """画像を用意できないときに黙って黒背景を作らないこと。

    ユーザー報告: 画像生成が全部失敗していたのに、真っ黒な動画が
    そのまま YouTube に投稿されていた。
    """

    def test_fallback_image_generator_is_gone(self):
        """黙って黒背景を返す関数自体を残さない。"""
        from app.services import image_generator

        assert not hasattr(image_generator, "generate_fallback_image")

    def test_imagen_models_rejected_upfront(self):
        """Imagen は Developer API キーで動かないので、実行前に弾く。"""
        from app.services.image_generator import (
            ImageUnavailableError,
            _generate_imagen,
        )

        with pytest.raises(ImageUnavailableError) as exc:
            _generate_imagen("prompt", "imagen-4.0-fast-generate-001")
        msg = str(exc.value)
        assert "imagen-4.0-fast-generate-001" in msg
        assert "gemini-2.5-flash-image" in msg  # 直し方を書く

    def test_default_shorts_model_is_usable(self):
        """既定モデルが Developer API キーで動くものであること。"""
        import app.config as config_module

        assert not config_module._DEFAULTS["shorts_image_model"].startswith("imagen")

    def test_shorts_generation_propagates_failure(
        self, pool_root, user_config, monkeypatch, tmp_path,
    ):
        """Shorts の画像生成が失敗したらステージごと失敗させる。"""
        from app.services import image_generator
        from app.services.image_generator import ImageUnavailableError

        user_config(shorts_title_bg_source="ai", shorts_scene_source="ai")
        monkeypatch.setattr(
            image_generator, "extract_scene_prompts", lambda *a, **k: ["p1", "p2"],
        )
        monkeypatch.setattr(
            image_generator, "generate_best_image",
            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("quota")),
        )
        with pytest.raises(ImageUnavailableError):
            image_generator.generate_images_for_story(
                "本文", "タイトル", tmp_path / "img", content_type="short",
            )

    def test_long_generation_propagates_failure(self, user_config, monkeypatch, tmp_path):
        """長編も同じ。フォールバック画像で先に進ませない。"""
        from app.services import image_generator
        from app.services.image_generator import ImageUnavailableError

        user_config(image_model="gemini-2.5-flash-image")
        monkeypatch.setattr(
            image_generator, "_generate_title_bg_prompt", lambda *a, **k: "bg",
        )
        monkeypatch.setattr(
            image_generator, "generate_image_ai",
            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("quota")),
        )
        with pytest.raises(ImageUnavailableError):
            image_generator.generate_images_for_story(
                "本文", "タイトル", tmp_path / "img2", content_type="long",
            )
