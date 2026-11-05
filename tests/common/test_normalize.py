"""Table-driven normalizer tests."""

from datetime import date
from decimal import Decimal

import pytest

from backend.common.normalize import (
    classify_lender_type,
    normalize_address,
    normalize_lender_name,
    parse_date,
    parse_money,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("$1,250,000.00", Decimal("1250000.00")),
        ("1250000", Decimal("1250000")),
        ("$ 450,000", Decimal("450000")),
        ("USD 5,000.5", Decimal("5000.5")),
        ("$1,000,000.00 dollars", Decimal("1000000.00")),
        ("(1,000.00)", Decimal("-1000.00")),
        ("-$20", Decimal("-20")),
        (1500, Decimal("1500")),
        (12.5, Decimal("12.5")),
        (Decimal("3"), Decimal("3")),
        ("", None),
        ("   ", None),
        (None, None),
        ("N/A", None),
        ("TBD", None),
        ("$", None),
        ("1.2.3", None),
        ("abc", None),
        (True, None),
    ],
)
def test_parse_money(raw, expected):
    assert parse_money(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("2021-03-04", date(2021, 3, 4)),
        ("03/04/2021", date(2021, 3, 4)),  # US month-first
        ("3/4/2021", date(2021, 3, 4)),
        ("13/04/2021", date(2021, 4, 13)),  # only valid day-first
        ("03/04/21", date(2021, 3, 4)),
        ("03/04/99", date(1999, 3, 4)),
        ("March 4, 2021", date(2021, 3, 4)),
        ("Mar 4 2021", date(2021, 3, 4)),
        ("4 March 2021", date(2021, 3, 4)),
        ("MARCH 4TH, 2021", date(2021, 3, 4)),
        ("  2021-03-04  ", date(2021, 3, 4)),
        (date(2021, 3, 4), date(2021, 3, 4)),
        ("02/30/2021", None),  # impossible
        ("13/13/2021", None),
        ("2021-13-01", None),
        ("March 2021", None),  # no day: ambiguous
        ("2021", None),
        ("", None),
        (None, None),
        ("not a date", None),
        ("Foo 4, 2021", None),
        (20210304, None),
    ],
)
def test_parse_date(raw, expected):
    assert parse_date(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Acme Lending, L.L.C.", "ACME LENDING LLC"),
        ("  acme   lending llc ", "ACME LENDING LLC"),
        ("Smith & Sons Inc.", "SMITH AND SONS INC"),
        ("First National Bank, N.A.", "FIRST NATIONAL BANK NA"),
        ("The Cape Cod Five Cents Savings Bank", "CAPE COD FIVE CENTS SAVINGS BANK"),
        ("O'Brien Capital Corp.", "OBRIEN CAPITAL CORP"),
        ("", None),
        (None, None),
        ("  . ", None),
    ],
)
def test_normalize_lender_name(raw, expected):
    assert normalize_lender_name(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("ACME LENDING LLC", "private_llc"),
        ("SOUTH SHORE BANK", "bank"),
        ("FIRST NATIONAL BANK NA", "bank"),
        ("BAY COMMUNITY FEDERAL CREDIT UNION", "credit_union"),
        ("JOHN Q SAMPLE", "individual"),
        ("JANE SAMPLE AND JOHN SAMPLE", "individual"),
        ("COMMONWEALTH OF MASSACHUSETTS", "other"),
        (None, "unknown"),
    ],
)
def test_classify_lender_type(raw, expected):
    assert classify_lender_type(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("12 Main Street", "12 MAIN ST"),
        ("12 main st.,", "12 MAIN ST"),
        ("45 Beacon Avenue, Apt. 3", "45 BEACON AVE APT 3"),
        ("7 North Shore Road #4", "7 N SHORE RD UNIT 4"),
        ("9  Water   Drive Mattapoisett, MA 02739", "9 WATER DR MATTAPOISETT MA 02739"),
        ("1 Ocean Boulevard", "1 OCEAN BLVD"),
        ("3 Cove Lane", "3 COVE LN"),
        ("", None),
        (None, None),
        ("   ", None),
    ],
)
def test_normalize_address(raw, expected):
    assert normalize_address(raw) == expected
