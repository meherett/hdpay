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

/** received - expected, signed: "+0.01" over, "-0.5" short, "0" exact; null without an expected amount. */
export function difference(
  received: string,
  expected: string | null,
  decimals: number
): { text: string; kind: 'over' | 'short' | 'exact' } | null {
  if (expected === null) {
    return null;
  }
  const delta = BigInt(received) - BigInt(expected);
  if (delta === 0n) {
    return { text: '0', kind: 'exact' };
  }
  const size = formatUnits((delta < 0n ? -delta : delta).toString(), decimals);
  return delta > 0n ? { text: `+${size}`, kind: 'over' } : { text: `\u2212${size}`, kind: 'short' };
}

/** "25.5" at 6 decimals -> "25500000". Null when the text is not a valid amount for those decimals. */
export function parseUnits(value: string, decimals: number): string | null {
  const match = /^(\d+)(?:\.(\d+))?$/.exec(value.trim());
  if (!match) {
    return null;
  }
  const [, whole, fraction = ''] = match;
  if (fraction.length > decimals) {
    return null;
  }
  return (BigInt(whole) * 10n ** BigInt(decimals) + BigInt(fraction.padEnd(decimals, '0') || '0')).toString();
}
