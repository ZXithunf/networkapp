from app.ratelimit import RateLimiter


class FakeClock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def test_allows_up_to_limit_then_blocks():
    limiter = RateLimiter(limit=3, window_seconds=60, clock=FakeClock())
    assert [limiter.allow("1.2.3.4") for _ in range(4)] == [True, True, True, False]


def test_clients_are_counted_separately():
    limiter = RateLimiter(limit=1, window_seconds=60, clock=FakeClock())
    assert limiter.allow("a") is True
    assert limiter.allow("b") is True
    assert limiter.allow("a") is False


def test_window_slides():
    clock = FakeClock()
    limiter = RateLimiter(limit=2, window_seconds=60, clock=clock)
    limiter.allow("a")
    clock.now += 30
    limiter.allow("a")
    assert limiter.allow("a") is False
    clock.now += 31  # first hit is now older than 60s
    assert limiter.allow("a") is True


def test_retry_after():
    clock = FakeClock()
    limiter = RateLimiter(limit=1, window_seconds=60, clock=clock)
    assert limiter.retry_after("a") == 0
    limiter.allow("a")
    clock.now += 50
    assert 1 <= limiter.retry_after("a") <= 11


def test_idle_clients_are_swept(monkeypatch):
    clock = FakeClock()
    limiter = RateLimiter(limit=5, window_seconds=60, clock=clock)
    for i in range(5):
        limiter.allow(f"old-{i}")
    clock.now += 120
    limiter._sweep(clock.now - 60)
    assert limiter._hits == {}


def test_default_is_30_per_minute():
    limiter = RateLimiter()
    assert (limiter.limit, limiter.window) == (30, 60.0)
