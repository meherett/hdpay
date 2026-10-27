import { InternalStatus, ProjectStatus } from '../services/api.interface';

/** Tag colour and a short explanation for every internal deposit status. */
export const STATUS: Record<InternalStatus, { color: string; label: string; hint: string }> = {
  pending: { color: 'blue', label: 'pending', hint: 'Address issued, inside its payment window' },
  expired: { color: 'default', label: 'expired', hint: 'Window closed with nothing received. Still watched.' },
  cancelled: { color: 'default', label: 'cancelled', hint: 'The project cancelled it while pending. Still watched.' },
  detected: { color: 'orange', label: 'detected', hint: 'Payment seen, waiting for confirmations' },
  partial: { color: 'gold', label: 'partial', hint: 'Underpaid. The rest can still arrive until the window ends' },
  confirmed: { color: 'green', label: 'confirmed', hint: 'Credited, waiting to be swept' },
  review: { color: 'red', label: 'review', hint: 'The rest never came. Not watched; the customer contacts support' },
  swept: { color: 'cyan', label: 'swept', hint: "Funds moved to the project's treasury" }
};

export const STATUSES = Object.keys(STATUS) as InternalStatus[];

/**
 * How to show a status, also one this version of the admin does not know yet (a newer API can add
 * one): its own name in a plain tag, so a page never breaks on it.
 */
export function statusOf(status: string): { color: string; label: string; hint: string } {
  return STATUS[status as InternalStatus] ?? { color: 'default', label: status, hint: '' };
}

/** Tag colour and label for a project's status. */
export const PROJECT_STATUS: Record<ProjectStatus, { color: string; label: string }> = {
  awaiting_keys: { color: 'gold', label: 'awaiting keys' },
  active: { color: 'green', label: 'active' },
  disabled: { color: 'default', label: 'disabled' },
  locked: { color: 'default', label: 'locked' }
};
