from app.guardrails.number_check import check_numbers

SOURCES = [
    {"columns": ["region", "total"], "rows": [["East", 1284330.5], ["West", 990123.25]]},
    {"scalars": {"growth_pct": 12.5, "share": 0.125, "change": -7.25}},
    {"answer": "The report lists 1180 customers in Q3."},
]


def test_exact_numbers_ok():
    r = check_numbers("East had $1,284,330.50 in revenue and West $990,123.25.", SOURCES)
    assert r.ok, r.unverified


def test_rounded_display_ok():
    assert check_numbers("East reached about $1.28M, West roughly $990K.", SOURCES).ok
    assert check_numbers("Growth was 12.5%.", SOURCES).ok


def test_ratio_shown_as_percent_ok():
    assert check_numbers("East's share is 12.5 percent.", [{"scalars": {"share": 0.125}}]).ok


def test_negative_change_described_in_words_ok():
    assert check_numbers("Revenue fell 7.25% month over month.", SOURCES).ok


def test_numbers_from_text_outputs_ok():
    assert check_numbers("There were 1,180 customers.", SOURCES).ok


def test_invented_number_flagged():
    r = check_numbers("West had $999,999 in revenue.", SOURCES)
    assert not r.ok and r.unverified == ["$999,999"]


def test_small_ints_years_and_labels_ignored():
    assert check_numbers("The top 3 regions in 2024 Q3 and FY2024 were strong.", SOURCES).ok


def test_question_numbers_allowed():
    assert check_numbers("Orders above 5000 are rare.", [], question="How many orders above 5000?").ok
