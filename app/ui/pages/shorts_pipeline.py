from __future__ import annotations

from app.models import STAGES_SHORT
from app.pipeline.executor import shorts_pipeline
from app.ui.pipeline_widgets import build_pipeline_page


def shorts_pipeline_page():
    """Shorts pipeline control and monitoring page."""
    build_pipeline_page(
        title="Shorts パイプライン", content_type="short", pipe=shorts_pipeline,
        stages=STAGES_SHORT, notify_prefix="Shorts ",
    )
