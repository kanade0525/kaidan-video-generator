"""YouTube Analytics dashboard page."""

from __future__ import annotations

import threading
from datetime import date, timedelta

from nicegui import ui

from app import database as db
from app.utils.log import get_logger

log = get_logger("kaidan.analytics_page")


def analytics_page():
    """YouTube Analytics dashboard."""
    ui.label("YouTube Analytics").classes("text-2xl font-bold mb-4")

    from app.services import youtube_uploader

    if not youtube_uploader.is_authenticated():
        ui.label(
            "YouTube未認証です。設定ページで認証を行ってください。"
        ).classes("text-red-500")
        ui.link("設定ページへ", "/settings").classes("text-blue-500 underline")
        return

    # Date range selector
    with ui.row().classes("gap-4 items-end mb-4"):
        period = ui.select(
            {"7": "過去7日", "28": "過去28日", "90": "過去90日"},
            value="28",
            label="期間",
        ).classes("w-40")

        status_label = ui.label("").classes("text-sm text-gray-500")
        refresh_btn = ui.button("データ取得", color="primary").props("size=sm")

    # Main content area
    content = ui.column().classes("w-full gap-4")

    def fetch_data():
        """Fetch analytics data from YouTube API and update UI."""
        refresh_btn.disable()
        status_label.text = "取得中..."
        status_label.classes(replace="text-sm text-blue-500")

        days = int(period.value)
        end = date.today() - timedelta(days=1)
        start = end - timedelta(days=days)

        def run():
            try:
                from app.services import youtube_analytics as yta

                channel_info = yta.get_channel_info()
                daily_data = yta.get_channel_analytics(start, end)
                video_data = yta.get_video_analytics(start, end)
                traffic_data = yta.get_traffic_sources(start, end)
                demographics = yta.get_demographics(start, end)
                geography = yta.get_geography(start, end)

                # Cache to DB
                db.upsert_channel_daily(daily_data)
                db.upsert_video_analytics(video_data, f"{start}~{end}")

                # Update UI (must be called from thread)
                try:
                    _render_dashboard(
                        content, channel_info, daily_data, video_data,
                        traffic_data, demographics, geography, days,
                    )
                    status_label.text = "最終取得: 完了"
                    status_label.classes(replace="text-sm text-green-500")
                except Exception:
                    pass
            except Exception as e:
                log.error("Analytics取得エラー: %s", e)
                try:
                    status_label.text = f"エラー: {e}"
                    status_label.classes(replace="text-sm text-red-500")
                except Exception:
                    pass
            finally:
                try:
                    refresh_btn.enable()
                except Exception:
                    pass

        threading.Thread(target=run, daemon=True).start()

    refresh_btn.on_click(fetch_data)

    # Load cached data on page load
    cached = db.get_channel_daily(28)
    if cached:
        with content:
            ui.label("キャッシュデータを表示中（最新データは「データ取得」ボタンで更新）").classes(
                "text-sm text-gray-400 italic"
            )
            _render_cached_channel(cached)


def _render_dashboard(
    container, channel_info, daily_data, video_data,
    traffic_data, demographics, geography, days,
):
    """Render full analytics dashboard."""
    container.clear()
    with container:
        # Channel overview
        _render_channel_overview(channel_info)
        # Monetization progress
        _render_monetization_progress(channel_info)
        # Daily trends
        _render_daily_charts(daily_data)
        # Video performance table
        _render_video_table(video_data)
        # Traffic sources
        _render_traffic_sources(traffic_data)
        # Demographics
        _render_demographics(demographics)
        # Geography
        _render_geography(geography)


def _render_channel_overview(info: dict):
    """Channel overview cards."""
    if not info:
        return

    ui.label("チャンネル概況").classes("text-lg font-bold mt-2")
    with ui.row().classes("gap-4 w-full"):
        _metric_card("登録者数", f"{info.get('subscriber_count', 0):,}", "people")
        _metric_card("総再生回数", f"{info.get('view_count', 0):,}", "play_circle")
        _metric_card("動画数", f"{info.get('video_count', 0):,}", "video_library")


def _metric_card(label: str, value: str, icon: str):
    """Single metric display card."""
    with ui.card().classes("p-4 flex-1 min-w-[150px]"):
        with ui.row().classes("items-center gap-2"):
            ui.icon(icon).classes("text-2xl text-blue-500")
            ui.label(label).classes("text-sm text-gray-500")
        ui.label(value).classes("text-2xl font-bold mt-1")


def _render_monetization_progress(info: dict):
    """Monetization requirements progress bars."""
    if not info:
        return

    ui.label("収益化条件").classes("text-lg font-bold mt-4")
    with ui.card().classes("w-full p-4"):
        subs = info.get("subscriber_count", 0)
        subs_pct = min(subs / 1000, 1.0)
        ui.label(f"登録者数: {subs:,} / 1,000").classes("text-sm mb-1")
        ui.linear_progress(value=subs_pct).props("rounded color=blue")

        # Total watch hours (view_count is total views, we approximate)
        # Note: actual watch hours need analytics data
        ui.label("").classes("mt-2")
        ui.label(
            "総再生時間 4,000時間の進捗は日次データから算出されます"
        ).classes("text-xs text-gray-400")


def _render_daily_charts(daily_data: list[dict]):
    """Render daily trend charts using echarts."""
    if not daily_data:
        return

    ui.label("日次推移").classes("text-lg font-bold mt-4")

    dates = [d.get("day", "") for d in daily_data]
    views = [d.get("views", 0) for d in daily_data]
    watch_mins = [round(d.get("estimatedMinutesWatched", 0), 1) for d in daily_data]
    subs_gained = [d.get("subscribersGained", 0) for d in daily_data]
    subs_lost = [d.get("subscribersLost", 0) for d in daily_data]
    net_subs = [g - l for g, l in zip(subs_gained, subs_lost)]

    # Views chart
    with ui.card().classes("w-full p-4"):
        ui.label("視聴回数").classes("text-sm font-bold mb-2")
        ui.echart({
            "xAxis": {"type": "category", "data": dates},
            "yAxis": {"type": "value"},
            "series": [{"data": views, "type": "bar", "color": "#3b82f6"}],
            "tooltip": {"trigger": "axis"},
            "grid": {"left": "10%", "right": "5%", "bottom": "15%"},
        }).classes("w-full h-64")

    with ui.row().classes("w-full gap-4"):
        # Watch time chart
        with ui.card().classes("flex-1 p-4"):
            ui.label("再生時間（分）").classes("text-sm font-bold mb-2")
            ui.echart({
                "xAxis": {"type": "category", "data": dates},
                "yAxis": {"type": "value"},
                "series": [{"data": watch_mins, "type": "line", "color": "#10b981", "smooth": True}],
                "tooltip": {"trigger": "axis"},
                "grid": {"left": "12%", "right": "5%", "bottom": "15%"},
            }).classes("w-full h-48")

        # Subscribers chart
        with ui.card().classes("flex-1 p-4"):
            ui.label("登録者純増").classes("text-sm font-bold mb-2")
            ui.echart({
                "xAxis": {"type": "category", "data": dates},
                "yAxis": {"type": "value"},
                "series": [{"data": net_subs, "type": "bar", "color": "#8b5cf6"}],
                "tooltip": {"trigger": "axis"},
                "grid": {"left": "12%", "right": "5%", "bottom": "15%"},
            }).classes("w-full h-48")

    # Summary stats for period
    total_views = sum(views)
    total_watch = sum(watch_mins)
    total_net_subs = sum(net_subs)
    watch_hours = total_watch / 60

    with ui.row().classes("gap-4 w-full"):
        _metric_card("期間合計視聴", f"{total_views:,}", "visibility")
        _metric_card("期間合計再生時間", f"{watch_hours:.1f}時間", "schedule")
        _metric_card("期間登録者純増", f"{total_net_subs:+,}", "trending_up")

    # Watch hours progress toward 4000
    with ui.card().classes("w-full p-4"):
        # Estimate cumulative from daily data
        cumulative_hours = watch_hours
        pct = min(cumulative_hours / 4000, 1.0)
        ui.label(f"再生時間（期間内）: {cumulative_hours:.1f} / 4,000 時間").classes("text-sm mb-1")
        ui.linear_progress(value=pct).props("rounded color=green")
        ui.label(
            "※ 収益化には直近12ヶ月で4,000時間必要です。表示は選択期間の合計です。"
        ).classes("text-xs text-gray-400 mt-1")


def _render_video_table(video_data: list[dict]):
    """Render per-video performance table."""
    if not video_data:
        return

    ui.label("動画別パフォーマンス").classes("text-lg font-bold mt-4")

    columns = [
        {"name": "title", "label": "タイトル", "field": "title", "align": "left", "sortable": True},
        {"name": "views", "label": "視聴回数", "field": "views", "sortable": True},
        {"name": "watch", "label": "再生時間(分)", "field": "watch", "sortable": True},
        {"name": "avg_dur", "label": "平均視聴(秒)", "field": "avg_dur", "sortable": True},
        {"name": "likes", "label": "高評価", "field": "likes", "sortable": True},
        {"name": "comments", "label": "コメント", "field": "comments", "sortable": True},
        {"name": "shares", "label": "共有", "field": "shares", "sortable": True},
    ]

    rows = []
    for v in video_data:
        title = v.get("title", v.get("video", ""))
        if len(title) > 40:
            title = title[:40] + "..."
        rows.append({
            "title": title,
            "views": v.get("views", 0),
            "watch": round(v.get("estimatedMinutesWatched", 0), 1),
            "avg_dur": round(v.get("averageViewDuration", 0)),
            "likes": v.get("likes", 0),
            "comments": v.get("comments", 0),
            "shares": v.get("shares", 0),
        })

    ui.table(columns=columns, rows=rows, row_key="title").classes(
        "w-full"
    ).props("dense flat")


def _render_traffic_sources(traffic_data: list[dict]):
    """Render traffic source breakdown."""
    if not traffic_data:
        return

    from app.services.youtube_analytics import TRAFFIC_SOURCE_LABELS

    ui.label("流入元").classes("text-lg font-bold mt-4")

    with ui.card().classes("w-full p-4"):
        labels = []
        values = []
        for t in traffic_data:
            source = t.get("insightTrafficSourceType", "OTHER")
            labels.append(TRAFFIC_SOURCE_LABELS.get(source, source))
            values.append(t.get("views", 0))

        ui.echart({
            "tooltip": {"trigger": "item"},
            "series": [{
                "type": "pie",
                "radius": ["40%", "70%"],
                "data": [
                    {"name": label, "value": val}
                    for label, val in zip(labels, values)
                ],
                "emphasis": {"itemStyle": {"shadowBlur": 10}},
            }],
        }).classes("w-full h-72")


def _render_demographics(demographics: list[dict]):
    """Render age/gender distribution."""
    if not demographics:
        return

    ui.label("視聴者属性").classes("text-lg font-bold mt-4")

    # Group by age
    age_groups = {}
    for d in demographics:
        age = d.get("ageGroup", "").replace("age", "").replace("AGE", "")
        gender = d.get("gender", "")
        pct = d.get("viewerPercentage", 0)
        if age not in age_groups:
            age_groups[age] = {}
        gender_label = {"MALE": "男性", "FEMALE": "女性"}.get(gender, gender)
        age_groups[age][gender_label] = pct

    with ui.card().classes("w-full p-4"):
        ages = sorted(age_groups.keys())
        male_data = [age_groups[a].get("男性", 0) for a in ages]
        female_data = [age_groups[a].get("女性", 0) for a in ages]

        ui.echart({
            "tooltip": {"trigger": "axis"},
            "legend": {"data": ["男性", "女性"]},
            "xAxis": {"type": "category", "data": ages},
            "yAxis": {"type": "value", "axisLabel": {"formatter": "{value}%"}},
            "series": [
                {"name": "男性", "type": "bar", "data": male_data, "color": "#3b82f6"},
                {"name": "女性", "type": "bar", "data": female_data, "color": "#ec4899"},
            ],
            "grid": {"left": "10%", "right": "5%", "bottom": "15%"},
        }).classes("w-full h-64")


def _render_geography(geography: list[dict]):
    """Render viewer geography."""
    if not geography:
        return

    ui.label("地域分布").classes("text-lg font-bold mt-4")

    columns = [
        {"name": "country", "label": "国", "field": "country", "align": "left"},
        {"name": "views", "label": "視聴回数", "field": "views", "sortable": True},
        {"name": "watch", "label": "再生時間(分)", "field": "watch", "sortable": True},
    ]
    rows = [
        {
            "country": g.get("country", ""),
            "views": g.get("views", 0),
            "watch": round(g.get("estimatedMinutesWatched", 0), 1),
        }
        for g in geography
    ]

    ui.table(columns=columns, rows=rows, row_key="country").classes(
        "w-full"
    ).props("dense flat")


def _render_cached_channel(cached: list[dict]):
    """Render basic charts from cached daily data."""
    dates = [d["date"] for d in cached]
    views = [d["views"] for d in cached]

    with ui.card().classes("w-full p-4"):
        ui.label("視聴回数（キャッシュ）").classes("text-sm font-bold mb-2")
        ui.echart({
            "xAxis": {"type": "category", "data": dates},
            "yAxis": {"type": "value"},
            "series": [{"data": views, "type": "bar", "color": "#3b82f6"}],
            "tooltip": {"trigger": "axis"},
            "grid": {"left": "10%", "right": "5%", "bottom": "15%"},
        }).classes("w-full h-48")
