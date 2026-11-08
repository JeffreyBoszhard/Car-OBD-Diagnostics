"""Server-sent events: the scanner pushes live data to the dashboard.

The dashboard used to poll /api/data ten times a second and /api/gauges
about thirty times a second. Every one of those requests rebuilt the entire
scan payload, which meant database reads, JSON state file reads, serial port
enumeration and even extra OBD queries - per request, per open tab.

Now one background publisher builds each frame once and hands it to every
connected browser over a single long-lived HTTP response. Three channels are
published:

  gauges  - rpm/speed only, at the fast gauge rate
  live    - the full live-data payload, a few times a second
  status  - connection/session state, on change and on a slow heartbeat

Each subscriber gets its own bounded queue. If a browser is backgrounded and
stops reading, its queue fills up and the oldest frames are dropped instead
of growing memory or stalling the publisher: live data is only interesting
while it is current.
"""
import json
import queue
import threading
import time

from config import STREAM_QUEUE_SIZE

_subscribers_lock = threading.Lock()
_subscribers = set()

# Last frame per channel, so a browser that connects mid-session gets the
# current picture immediately instead of waiting for the next tick.
_last_frames_lock = threading.Lock()
_last_frames = {}

CHANNELS = ("gauges", "live", "status", "notice")


class Subscriber:
    """One connected browser."""

    __slots__ = ("created_at", "queue")

    def __init__(self):
        self.queue = queue.Queue(maxsize=STREAM_QUEUE_SIZE)
        self.created_at = time.time()

    def put(self, message):
        try:
            self.queue.put_nowait(message)
        except queue.Full:
            # Drop the oldest frame and keep the newest one.
            try:
                self.queue.get_nowait()
            except queue.Empty:
                pass
            try:
                self.queue.put_nowait(message)
            except queue.Full:
                pass


def subscriber_count():
    with _subscribers_lock:
        return len(_subscribers)


def has_subscribers():
    return subscriber_count() > 0


def subscribe():
    subscriber = Subscriber()
    with _subscribers_lock:
        _subscribers.add(subscriber)
    return subscriber


def unsubscribe(subscriber):
    with _subscribers_lock:
        _subscribers.discard(subscriber)


def _format_sse(channel, payload):
    return f"event: {channel}\ndata: {json.dumps(payload, separators=(',', ':'))}\n\n"


def publish(channel, payload, remember=True):
    """Send one frame to every connected browser."""
    message = _format_sse(channel, payload)

    if remember:
        with _last_frames_lock:
            _last_frames[channel] = message

    with _subscribers_lock:
        targets = tuple(_subscribers)

    for subscriber in targets:
        subscriber.put(message)


def replay_last_frames():
    """The current frame of every channel, for a browser that just connected."""
    with _last_frames_lock:
        return [_last_frames[channel] for channel in CHANNELS if channel in _last_frames]


def clear_last_frames():
    with _last_frames_lock:
        _last_frames.clear()


def stream(subscriber, heartbeat_seconds=15.0):
    """Generator handed to Flask as the streaming response body."""
    try:
        yield "retry: 2000\n\n"
        # Hand the newcomer the current frame of each channel straight away,
        # so the dashboard paints without waiting for the next tick.
        yield from replay_last_frames()

        while True:
            try:
                yield subscriber.queue.get(timeout=heartbeat_seconds)
            except queue.Empty:
                # A comment line keeps proxies and browsers from treating a
                # quiet connection as dead.
                yield ": keep-alive\n\n"
    finally:
        unsubscribe(subscriber)
