"""Tests for the EventBus publish-subscribe system."""

from app.pipeline.events import EventBus


class TestEventBus:
    def test_subscribe_and_publish(self):
        bus = EventBus()
        received = []
        bus.subscribe("test", lambda d: received.append(d))
        bus.publish("test", {"key": "value"})
        assert received == [{"key": "value"}]

    def test_multiple_subscribers(self):
        bus = EventBus()
        results = []
        bus.subscribe("evt", lambda d: results.append("a"))
        bus.subscribe("evt", lambda d: results.append("b"))
        bus.publish("evt")
        assert sorted(results) == ["a", "b"]

    def test_unsubscribe(self):
        bus = EventBus()
        received = []
        cb = lambda d: received.append(d)  # noqa: E731
        bus.subscribe("test", cb)
        bus.unsubscribe("test", cb)
        bus.publish("test", {"x": 1})
        assert received == []

    def test_unsubscribe_missing_callback(self):
        bus = EventBus()
        # Should not raise
        bus.unsubscribe("test", lambda d: None)

    def test_publish_no_subscribers(self):
        bus = EventBus()
        # Should not raise
        bus.publish("nonexistent", {"data": 1})

    def test_publish_none_data(self):
        bus = EventBus()
        received = []
        bus.subscribe("test", lambda d: received.append(d))
        bus.publish("test")
        assert received == [{}]

    def test_subscriber_exception_does_not_break_others(self):
        bus = EventBus()
        received = []

        def bad_cb(d):
            raise ValueError("boom")

        bus.subscribe("test", bad_cb)
        bus.subscribe("test", lambda d: received.append("ok"))
        bus.publish("test")
        assert received == ["ok"]

    def test_different_event_types_isolated(self):
        bus = EventBus()
        a_received = []
        b_received = []
        bus.subscribe("a", lambda d: a_received.append(d))
        bus.subscribe("b", lambda d: b_received.append(d))
        bus.publish("a", {"type": "a"})
        assert a_received == [{"type": "a"}]
        assert b_received == []
