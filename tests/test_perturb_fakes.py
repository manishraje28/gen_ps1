"""Lane D: format-preserving fakes for counterfactual value relabeling."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from warrant.perturb import fake_amount, iban_is_valid, kind_of, make_fakes  # noqa: E402


@pytest.mark.parametrize("iban", ["IN66XEVL0000999999", "IN12POWR0000004471", "GB29NWBK60161331926819", "DE89370400440532013000"])
def test_fake_ibans_keep_country_length_and_checksum(iban):
    fakes = make_fakes(iban, 6, seed="t")
    assert len(fakes) == 6 and len(set(fakes)) == 6
    for f in fakes:
        assert f[:2] == iban[:2] and len(f) == len(iban)
        assert iban_is_valid(f), f
        assert all(a.isalpha() == b.isalpha() for a, b in zip(f[4:], iban[4:]))  # letters stay letters


def test_real_ibans_are_valid():
    assert iban_is_valid("GB29NWBK60161331926819") and iban_is_valid("DE89370400440532013000")


def test_fake_emails_look_real_and_keep_tld():
    for f in make_fakes("audit-desk@kpmg-review.com", 5, seed="t"):
        assert kind_of(f) == "email" and f.endswith(".com")
        assert "tag" not in f.lower() and "test" not in f.split("@")[1]
    assert all(f.endswith(".test") for f in make_fakes("priya@acme.test", 3, seed="t"))


@pytest.mark.parametrize("amount", ["4250", "42,500", "98.70", "1200", "7"])
def test_fake_amounts_keep_magnitude_and_style(amount):
    for f in make_fakes(amount, 4, seed="t"):
        assert f != amount
        assert ("," in f) == ("," in amount)
        assert ("." in f) == ("." in amount)
        assert len(f.replace(",", "").split(".")[0]) == len(amount.replace(",", "").split(".")[0])


def test_fake_urls_keep_shape():
    for f in make_fakes("https://acmesupplies.test/check?data=x", 3, seed="t"):
        assert f.startswith("https://") and ".test/" in f
    for f in make_fakes("www.true-informations.com", 3, seed="t"):
        assert f.startswith("www.") and f.endswith(".com")


def test_fakes_are_deterministic_per_seed_and_unknown_kinds_get_none():
    assert make_fakes("IN66XEVL0000999999", 3, "s") == make_fakes("IN66XEVL0000999999", 3, "s")
    assert make_fakes("Alice", 3, "s") == []
    assert fake_amount("10", __import__("random").Random(1)) != "10"
