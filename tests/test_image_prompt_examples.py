"""Tests for image prompt example diversity.

Regression: the Gemini scene-prompt instruction previously gave only two
female-coded examples (老婆, 黒髪の女), which biased the LLM toward
generating similar-looking women across stories. This test locks in that
the instruction now includes diverse subject examples.
"""

from unittest.mock import MagicMock, patch

from app.services import image_generator as ig


def _run_extract(captured: dict):
    """Invoke extract_scene_prompts with a stub Gemini client; capture sent prompt."""

    fake_response = MagicMock()
    fake_response.text = "a\nb\nc"

    fake_client = MagicMock()

    def fake_generate_content(*, model, contents):
        captured["prompt"] = contents
        return fake_response

    fake_client.models.generate_content.side_effect = fake_generate_content

    with patch.object(ig, "get_gemini_image", return_value=fake_client), \
         patch.object(ig, "cfg_get", return_value="gemini-2.5-flash"):
        ig.extract_scene_prompts("本文ダミー", "テスト", num_scenes=3)


def test_prompt_includes_diverse_subject_examples():
    """Instruction must list multiple non-female subject examples."""
    captured = {}
    _run_extract(captured)
    prompt = captured["prompt"]

    # Non-female/non-human/empty examples that should now appear
    assert "白髪の老人" in prompt
    assert "赤い着物の少女" in prompt
    assert "黒猫" in prompt
    assert "黒電話" in prompt
    assert "学校の教室" in prompt
    assert "作業着姿の中年男性" in prompt


def test_prompt_keeps_female_example_but_not_only_female():
    """One female example remains, but must not be the sole subject option."""
    captured = {}
    _run_extract(captured)
    prompt = captured["prompt"]

    assert "黒髪を垂らした女" in prompt
    assert "毎回同じ方向に寄せない" in prompt
