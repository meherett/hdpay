import { DepositInterface, DiscountInterface } from '../services/api.interface';

/** How each discount state is shown. */
export const DISCOUNT_STATUS: Record<DiscountInterface['status'], { label: string; color: string; hint: string }> = {
  active: { label: 'running', color: 'green', hint: 'Paid in full before it ends, the discount holds.' },
  kept: { label: 'kept', color: 'blue', hint: 'Paid in time: the discounted price was charged.' },
  lapsed: { label: 'ended unpaid', color: 'red', hint: 'It ended before the deposit was paid: the full price is due.' },
  honored: { label: 'kept by support', color: 'purple', hint: 'It had ended, and support kept it anyway.' }
};

/** A USD price less a percent, to the cent half up, as the server works it out: 100 at 25% off is "75.00". */
export function discountedPrice(price: string, percent: number): string {
  const cents = Math.round(Number(price) * 100);
  return (Math.round((cents * (100 - percent)) / 100) / 100).toFixed(2);
}

/** A discount that ended before the deposit was paid: the full price is due, unless support keeps it. */
export function discountEnded(d: DepositInterface): boolean {
  const discount = d.discount;
  return !!discount && ['active', 'lapsed'].includes(discount.status) && Date.parse(discount.ends_at) <= Date.now();
}

/** The discount's state as it is now: a running discount past its end shows as ended unpaid. */
export function discountState(d: DepositInterface): DiscountInterface['status'] | null {
  if (!d.discount) {
    return null;
  }
  return d.discount.status === 'active' && discountEnded(d) ? 'lapsed' : d.discount.status;
}

/** Its discount ended before it was paid, and support did not keep it: the original price is due. */
export function discountOver(d: DepositInterface): boolean {
  return !!d.discount && !['confirmed', 'swept'].includes(d.internal_status) && discountState(d) === 'lapsed';
}

/** The price due now, in base units: the original price once its discount ended unpaid, else the expected amount. */
export function due(d: DepositInterface): string | null {
  return discountOver(d) ? d.discount!.full_amount : d.expected_amount;
}

/**
 * What the customer still owes, in base units: nothing once paid; against the full price once a
 * discount has ended unpaid (or the discounted one when `keep`); else against the expected amount.
 * Null when the deposit accepts any amount.
 */
export function owed(d: DepositInterface, keep = false): string | null {
  if (['confirmed', 'swept'].includes(d.internal_status)) {
    return '0';
  }
  let expected = d.expected_amount;
  if (discountEnded(d) && d.discount) {
    expected = keep ? d.discount.amount : d.discount.full_amount;
  }
  if (expected === null) {
    return null;
  }
  const rest = BigInt(expected) - BigInt(d.received_amount);
  return (rest > 0n ? rest : 0n).toString();
}
