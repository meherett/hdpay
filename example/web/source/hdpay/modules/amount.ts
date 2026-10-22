/**
 * Amounts travel as strings of base units ("25000000" = 25 USDT at 6 decimals).
 * BigInt keeps them exact: 18-decimal amounts do not fit in a JavaScript number.
 */
export function formatUnits(value: string | null, decimals: number): string {
  if (value === null) {
    return '';
  }
  const unit = 10n ** BigInt(decimals);
  const base = BigInt(value);
  const whole = base / unit;
  const fraction = base % unit;
  if (fraction === 0n) {
    return whole.toString();
  }
  return `${whole}.${fraction.toString().padStart(decimals, '0').replace(/0+$/, '')}`;
}
