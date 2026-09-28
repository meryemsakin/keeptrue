from pathlib import Path

from tinylib.dates import parse_date

FIXTURE = Path(__file__).parent / "fixtures" / "sample_dates.txt"


def test_parses_every_sample_date():
    values = [line for line in FIXTURE.read_text().splitlines() if line.strip()]
    assert all(parse_date(value) is not None for value in values)


def test_rejects_impossible_dates():
    assert parse_date("2026-13-01") is None
    assert parse_date("31/02/2026") is None
