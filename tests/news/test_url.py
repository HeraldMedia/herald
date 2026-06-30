from herald.validator.news.url import canonicalize, article_id, host_of


def test_lowercases_scheme_and_host():
    assert canonicalize("HTTP://Example.COM/Path") == "http://example.com/Path"


def test_strips_fragment_and_default_port():
    assert canonicalize("https://example.com:443/a#section") == "https://example.com/a"
    assert canonicalize("http://example.com:80/a") == "http://example.com/a"


def test_strips_tracking_params_keeps_real_ones():
    out = canonicalize("https://nytimes.com/x?utm_source=tw&id=7&fbclid=abc")
    assert out == "https://nytimes.com/x?id=7"


def test_drops_trailing_slash_on_path():
    assert canonicalize("https://example.com/a/b/") == "https://example.com/a/b"
    # root stays as bare host
    assert canonicalize("https://example.com/") == "https://example.com"


def test_sorts_query_for_determinism():
    a = canonicalize("https://example.com/x?b=2&a=1")
    b = canonicalize("https://example.com/x?a=1&b=2")
    assert a == b == "https://example.com/x?a=1&b=2"


def test_article_id_is_stable_and_hex():
    aid = article_id("https://example.com/x?utm_source=tw&a=1")
    assert aid == article_id("https://EXAMPLE.com/x?a=1#frag")
    assert len(aid) == 64


def test_host_of():
    assert host_of("https://www.nytimes.com/2026/01/01/x") == "www.nytimes.com"


def test_idn_host_stable_with_punycode():
    assert canonicalize("http://☃.com/a") == canonicalize("http://xn--n3h.com/a")


def test_ipv6_brackets_preserved():
    assert canonicalize("http://[2606:4700:4700::1111]/a") == "http://[2606:4700:4700::1111]/a"
