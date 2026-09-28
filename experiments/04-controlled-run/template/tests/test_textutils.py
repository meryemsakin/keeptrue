from tinylib.textutils import normalize_space, title_case, truncate


def test_normalize_space():
    assert normalize_space("  a \n b\t c  ") == "a b c"


def test_title_case():
    assert title_case("hello   wORLD") == "Hello WORLD"


def test_truncate():
    assert truncate("abcdef", 4) == "abc…"
    assert truncate("abc", 4) == "abc"
