"""Synthetic Belgian identifiers have to be correctly shaped, not merely plausible."""

from __future__ import annotations

import pytest

from peppol_e_invoice_intake.corpus.identifiers import (
    belgian_enterprise_number,
    belgian_iban,
    belgian_vat_number,
    is_valid_enterprise_number,
    structured_payment_reference,
)


@pytest.mark.parametrize("base", [4030405, 5398015, 4561237, 777123, 1, 99999999])
def test_generated_enterprise_numbers_pass_the_mod97_check(base: int):
    assert is_valid_enterprise_number(belgian_enterprise_number(base))


def test_enterprise_number_is_always_ten_digits():
    assert belgian_enterprise_number(1) == "00000001" + f"{97 - 1 % 97:02d}"
    assert len(belgian_enterprise_number(1)) == 10


def test_known_real_enterprise_number_validates():
    """0403040542 is a genuine, well-known Belgian enterprise number shape."""
    assert is_valid_enterprise_number("0403040542")


def test_a_tampered_check_digit_is_rejected():
    assert not is_valid_enterprise_number("0403040500")


def test_vat_number_prefixes_the_country_code():
    assert belgian_vat_number("0403040542") == "BE0403040542"


def test_generated_iban_matches_a_known_valid_belgian_iban():
    assert belgian_iban(539, 75470) == "BE68539007547034"


@pytest.mark.parametrize(
    ("bank", "account"), [(539, 75470), (1, 1), (999, 9999999), (310, 123456)]
)
def test_iban_check_digits_satisfy_iso13616(bank: int, account: int):
    iban = belgian_iban(bank, account)
    rearranged = iban[4:] + iban[:4]
    numeric = "".join(str(int(ch, 36)) for ch in rearranged)
    assert int(numeric) % 97 == 1


def test_iban_national_check_digits_are_correct():
    iban = belgian_iban(539, 75470)
    body, national_check = iban[4:14], iban[14:16]
    assert int(national_check) == (int(body) % 97 or 97)


def test_structured_payment_reference_is_mod97_checked():
    reference = structured_payment_reference(2026, 144)
    digits = reference.strip("+").replace("/", "")
    assert len(digits) == 12
    assert int(digits[10:]) == (int(digits[:10]) % 97 or 97)


def test_structured_payment_reference_formatting():
    assert structured_payment_reference(2026, 144).startswith("+++")
    assert structured_payment_reference(2026, 144).endswith("+++")
