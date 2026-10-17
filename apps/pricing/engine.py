from __future__ import annotations

from decimal import ROUND_FLOOR, ROUND_HALF_UP, Decimal

from apps.ingestion.models import Offer
from apps.pricing.models import PriceAction, PriceRule, RoundingMode

TWOPLACES = Decimal("0.01")


def _round_price(amount: Decimal, mode: str) -> Decimal:
    if mode in {RoundingMode.END_99, RoundingMode.END_95}:
        suffix = Decimal("0.99") if mode == RoundingMode.END_99 else Decimal("0.95")
        whole = amount.to_integral_value(rounding=ROUND_FLOOR)
        if amount == whole:
            whole -= 1
        return whole + suffix
    return amount.quantize(TWOPLACES, rounding=ROUND_HALF_UP)


def rule_matches(rule: PriceRule, offer: Offer) -> bool:
    if rule.supplier_id and rule.supplier_id != offer.supplier_id:
        return False
    if rule.category_id and rule.category_id != offer.category_id:
        return False
    if rule.brand_id and rule.brand_id != offer.brand_id:
        return False
    price = offer.purchase_price
    if rule.min_purchase_price is not None:
        if price is None or price < rule.min_purchase_price:
            return False
    if rule.max_purchase_price is not None:
        if price is None or price > rule.max_purchase_price:
            return False
    return True


def apply_rule(rule: PriceRule, current: Decimal) -> Decimal:
    if rule.action == PriceAction.MARKUP_PERCENT:
        current = current * (Decimal("1") + (rule.value / Decimal("100")))
    elif rule.action == PriceAction.MARKUP_FIXED:
        current = current + rule.value
    elif rule.action == PriceAction.SET_PRICE:
        current = rule.value

    mode = rule.rounding
    if rule.action == PriceAction.ROUND and mode == RoundingMode.NONE:
        mode = RoundingMode.CENT
    if mode != RoundingMode.NONE:
        return _round_price(current, mode)
    return current.quantize(TWOPLACES, rounding=ROUND_HALF_UP)


def compute_selling_price(offer: Offer, rules: list[PriceRule] | None = None) -> Decimal | None:
    # Pardavimo bazė: RRP (jei yra – pvz. FOX 'RRP EUR' su PVM),
    # kitaip savikaina + markup taisyklės.
    base = offer.rrp_price if offer.rrp_price is not None else offer.purchase_price
    if base is None:
        return None
    current = base
    rules = rules if rules is not None else list(PriceRule.objects.filter(is_active=True))
    for rule in rules:
        if rule_matches(rule, offer):
            current = apply_rule(rule, current)
    return current.quantize(TWOPLACES, rounding=ROUND_HALF_UP)


def apply_pricing(offers=None) -> int:
    rules = list(PriceRule.objects.filter(is_active=True))
    queryset = offers if offers is not None else Offer.objects.all()
    updated = 0
    for offer in queryset.iterator():
        new_price = compute_selling_price(offer, rules)
        if offer.selling_price != new_price:
            offer.selling_price = new_price
            offer.save(update_fields=["selling_price", "updated_at"])
            updated += 1
    return updated
