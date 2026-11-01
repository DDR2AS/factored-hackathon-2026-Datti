"""Synthetic exchange rates for amount_usd (owner: arturo).

SYNTHETIC. Fixed rates used by the demo gateway (to fill ``Txn.amount_usd``) and by the
provisional ranker (to compare an amount said in one currency with a charge in another).
ARS 350 and COP 4000 are the fixed rates the dataset analysis found in the generator;
MXN 18.0 is the demo assumption (in the dataset, Mexican products are in USD). BRL 5.0 is
an assumption so that "R$ 35,90" can be compared at all; no demo customer holds BRL.
When the gold tables arrive, the real rate comes from the gateway, not from here.
"""

from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

# Units of local currency per 1 USD.
FX_PER_USD: dict[str, Decimal] = {
    "USD": Decimal("1"),
    "MXN": Decimal("18.0"),
    "COP": Decimal("4000"),
    "ARS": Decimal("350"),
    "BRL": Decimal("5.0"),
}

_CENT = Decimal("0.01")


def to_usd(amount, currency: str | None) -> Decimal | None:
    """Convert ``amount`` (Decimal, str or number) in ``currency`` to USD, rounded to cents.

    Contract: returns None when the currency is unknown or missing, or the amount is not a
    number. Never raises for bad input; never guesses a currency.
    """
    if currency is None:
        return None
    rate = FX_PER_USD.get(currency.upper())
    if rate is None:
        return None
    try:
        value = Decimal(str(amount))
    except (InvalidOperation, ValueError):
        return None
    return (value / rate).quantize(_CENT, rounding=ROUND_HALF_UP)
