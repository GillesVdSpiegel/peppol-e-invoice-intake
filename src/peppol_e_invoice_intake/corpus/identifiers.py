"""Generate correctly-shaped Belgian identifiers for synthetic invoices.

Peppol rule PEPPOL-COMMON-R043 runs a mod-97 check on 0208 enterprise numbers, so
a merely plausible-looking number produces a corpus that fails validation for
reasons unrelated to extraction quality. IBANs get the same treatment: both the
Belgian national check digits and the ISO 13616 check digits are computed rather
than invented.
"""

from __future__ import annotations


def belgian_enterprise_number(base: int) -> str:
    """Return a 10-digit enterprise number whose check digits satisfy mod-97.

    The rule OpenPEPPOL implements is: check digits = 97 - (first eight digits
    mod 97). `base` supplies those first eight digits.
    """
    first_eight = f"{base:08d}"
    if len(first_eight) != 8:
        raise ValueError(f"base must fit in eight digits, got {base}")
    check = 97 - (int(first_eight) % 97)
    return f"{first_eight}{check:02d}"


def is_valid_enterprise_number(value: str) -> bool:
    digits = value.replace(".", "").replace(" ", "")
    if len(digits) != 10 or not digits.isdigit():
        return False
    return int(digits[8:10]) == 97 - (int(digits[:8]) % 97)


def belgian_vat_number(enterprise_number: str) -> str:
    """BT-31 / BT-48 form: the enterprise number prefixed with the country code."""
    return f"BE{enterprise_number}"


def _iban_check_digits(country: str, bban: str) -> str:
    """ISO 13616: move the country code and '00' to the end, then 98 - mod 97."""
    rearranged = f"{bban}{country}00"
    numeric = "".join(str(int(ch, 36)) for ch in rearranged)
    return f"{98 - (int(numeric) % 97):02d}"


def belgian_iban(bank_code: int, account: int) -> str:
    """Build a structurally valid BE IBAN.

    The Belgian BBAN is twelve digits: a three-digit bank code, a seven-digit
    account number, and two national check digits equal to the first ten digits
    mod 97 (with a remainder of zero represented as 97).
    """
    body = f"{bank_code:03d}{account:07d}"
    if len(body) != 10:
        raise ValueError("bank_code must fit in 3 digits and account in 7")
    national_check = int(body) % 97 or 97
    bban = f"{body}{national_check:02d}"
    return f"BE{_iban_check_digits('BE', bban)}{bban}"


def structured_payment_reference(year: int, sequence: int) -> str:
    """Belgian structured communication: +++ddd/dddd/ddddd+++, mod-97 checked.

    The first ten digits carry the reference; the last two are that value mod 97,
    with a remainder of zero written as 97.
    """
    body = f"{year % 100:02d}{sequence:08d}"
    check = int(body) % 97 or 97
    digits = f"{body}{check:02d}"
    return f"+++{digits[:3]}/{digits[3:7]}/{digits[7:]}+++"
