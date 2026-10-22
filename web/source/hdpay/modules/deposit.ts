import { DepositInterface } from '../services/api.interface';
import { formatUnits } from './amount';

/** What deleting a deposit address means, for its confirmation: a warning when it holds unswept coins. */
export function deleteWarning(d: DepositInterface): string {
  const unswept = BigInt(d.received_amount) > 0n && d.internal_status !== 'swept';
  return unswept
    ? `Delete this address? It holds ${formatUnits(d.received_amount, d.decimals)} ${d.asset} that has not been swept: ` +
        'HDPay stops watching and sweeping it, and the coins stay on chain, reachable only with the master key.'
    : 'Delete this address? HDPay stops watching it, and it is gone from the list. This cannot be undone.';
}
