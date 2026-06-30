from herald.validator.news.attribution import Candidate, resolve_attribution


def cand(uid, article_id, outlet, brief, epoch, usd, passed=True):
    return Candidate(uid=uid, article_id=article_id, outlet_id=outlet,
                     brief_id=brief, commit_epoch=epoch, usd=usd, passed=passed)


def test_same_url_earliest_commit_wins():
    cands = [
        cand(1, "A", "nyt", "b1", epoch=5, usd=500),
        cand(2, "A", "nyt", "b1", epoch=3, usd=500),  # committed earlier
    ]
    usd = resolve_attribution(cands)
    assert usd == {2: 500.0, 1: 0.0}


def test_organic_or_failed_not_paid():
    cands = [
        cand(1, "A", "nyt", "b1", epoch=None, usd=500),       # no commitment
        cand(2, "B", "nyt", "b1", epoch=4, usd=500, passed=False),  # failed oracle
    ]
    usd = resolve_attribution(cands)
    assert usd == {1: 0.0, 2: 0.0}


def test_one_paid_placement_per_outlet_brief():
    cands = [
        cand(1, "A", "nyt", "b1", epoch=2, usd=500),  # distinct URLs, same outlet+brief
        cand(2, "B", "nyt", "b1", epoch=6, usd=500),
    ]
    usd = resolve_attribution(cands)
    assert usd == {1: 500.0, 2: 0.0}  # only earliest placement on the outlet/brief


def test_distinct_outlets_both_paid():
    cands = [
        cand(1, "A", "nyt", "b1", epoch=2, usd=500),
        cand(2, "B", "guardian", "b1", epoch=6, usd=250),
    ]
    usd = resolve_attribution(cands)
    assert usd == {1: 500.0, 2: 250.0}


def test_tie_breaks_on_uid():
    cands = [
        cand(3, "A", "nyt", "b1", epoch=5, usd=500),
        cand(1, "A", "nyt", "b1", epoch=5, usd=500),
    ]
    usd = resolve_attribution(cands)
    assert usd == {1: 500.0, 3: 0.0}


def test_multiple_claims_sum_for_winner():
    cands = [
        cand(1, "A", "nyt", "b1", epoch=2, usd=500),
        cand(1, "B", "guardian", "b1", epoch=2, usd=250),
    ]
    usd = resolve_attribution(cands)
    assert usd == {1: 750.0}
