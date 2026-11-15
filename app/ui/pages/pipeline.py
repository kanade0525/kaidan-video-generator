from __future__ import annotations

from app.models import STAGES
from app.pipeline.executor import pipeline
from app.ui.pipeline_widgets import build_pipeline_page


def pipeline_page():
    """Pipeline control and monitoring page."""
    build_pipeline_page(
        title="パイプライン", content_type="long", pipe=pipeline, stages=STAGES,
    )
