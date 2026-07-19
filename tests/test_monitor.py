from monitor import find_new_showings, is_relevant, stable_key


def test_relevance_requires_movie_and_imax():
    assert is_relevant("האודיסאה IMAX יום חמישי 20:30")
    assert is_relevant("The Odyssey — IMAX")
    assert not is_relevant("האודיסאה 2D")
    assert not is_relevant("סרט אחר IMAX")


def test_first_run_creates_baseline_without_new_alerts():
    current = [{"context": "האודיסאה IMAX", "label": "20:30", "url": "https://x"}]
    assert find_new_showings(None, current) == []


def test_detects_only_new_items():
    old = {"showings": [
        {"context": "האודיסאה IMAX Thu", "label": "20:30", "url": "https://x/1"}
    ]}
    current = old["showings"] + [
        {"context": "האודיסאה IMAX Fri", "label": "17:00", "url": "https://x/2"}
    ]
    assert find_new_showings(old, current) == [current[1]]


def test_stable_key_is_order_independent():
    a = {"context": "x", "label": "y", "url": "z"}
    b = {"url": "z", "context": "x", "label": "y"}
    assert stable_key(a) == stable_key(b)
