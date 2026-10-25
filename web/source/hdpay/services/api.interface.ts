export type InternalStatus = 'pending' | 'expired' | 'detected' | 'partial' | 'confirmed' | 'review' | 'swept';

/**
 * The discount a deposit was opened with, given by the project for that customer: active while it
 * runs, kept when paid in time, lapsed when it ended unpaid (the full price is due), honored when
 * support kept it after the end.
 */
export interface DiscountInterface {
  percent: number;
  ends_at: string;
  /** Base units: the full price, and the discounted one. */
  full_amount: string;
  amount: string;
  status: 'active' | 'kept' | 'lapsed' | 'honored';
}

/** One payment counted on a deposit. */
export interface PaymentInterface {
  /** null when it was found in the address's balance (coins sent through a contract) */
  tx_hash: string | null;
  tx_url: string | null;
  block_number: number | null;
  /** base units */
  amount: string;
  /** the block's time */
  paid_at: string | null;
  /** scan (the watcher), check (a support check) or balance */
  source: 'scan' | 'check' | 'balance';
  confirmed: boolean;
}

/** A support check of the deposit's address, run by the chain's watcher. */
export interface CheckInterface {
  status: 'waiting' | 'done';
  requested_at: string;
  requested_by: string;
  checked_at: string | null;
  from_block: number | null;
  to_block: number | null;
  /** while it runs: the next block it reads */
  next_block: number | null;
  /** false: the chain could not tell block times, so only recent blocks were read */
  exact: boolean | null;
  /** every payment on the chain in that range */
  found: { tx_hash: string | null; amount: string; paid_at: string | null }[];
  /** newly counted by this check, base units */
  counted: string | null;
  from_balance: string | null;
  error: string | null;
}

/** The deposit's address as the chain sees it now. */
export interface ChainViewInterface {
  /** base units; null when the chain could not be read */
  balance: string | null;
  error: string | null;
  /** when the chain's watcher last ran; null when it never did */
  watcher_seen_at: string | null;
}

export interface DepositInterface {
  id: string;
  project_id: string;
  project_name: string | null;
  account_id: string;
  /** What the customer is paying for: the stored value, and the name to show. */
  product: string | null;
  product_name: string | null;
  chain: string;
  chain_name: string;
  family: string;
  asset: string;
  /** ERC20 / TRC20 for a token; null for the native coin */
  standard: string | null;
  decimals: number;
  /** UTC day in the derivation path, yyyy-mm-dd. */
  derivation_date: string;
  /** The account's index for that day. */
  index: number;
  /** Full derivation path, e.g. m/195'/42/2026/10/20/0 */
  path: string;
  address: string;
  address_url: string;
  /** What the customer sees: pending, expired, confirming, paid, contact_support. */
  status: string;
  internal_status: InternalStatus;
  /** over, under or exact against the expected amount; null before anything arrived */
  difference: 'over' | 'under' | 'exact' | null;
  late: boolean;
  /** Base units as strings; null means any amount is accepted. */
  expected_amount: string | null;
  received_amount: string;
  tx_hash: string | null;
  tx_url: string | null;
  block_number: number | null;
  fee_tx_hash: string | null;
  fee_tx_url: string | null;
  sweep_tx_hash: string | null;
  sweep_tx_url: string | null;
  /** Why the sweeper stopped on this deposit; it is skipped until released. */
  sweep_hold: string | null;
  swept_at: string | null;
  confirmations: number;
  created_at: string;
  expires_at: string;
  expires_in_seconds: number;
  confirmed_at: string | null;
  discount: DiscountInterface | null;
  /** every payment counted, oldest first */
  payments: PaymentInterface[];
  /** false: the coin was taken out of chains.yaml after this deposit opened */
  asset_supported: boolean;
  check: CheckInterface | null;
}

export interface DepositListInterface {
  total: number;
  skip: number;
  limit: number;
  items: DepositInterface[];
}

/** Support's terms when reopening a deposit in review; both default to the usual. */
export interface ReopenInterface {
  /** Base units still to send; the expected amount becomes received + this. */
  remaining_amount?: string;
  /** Countdown, from now. */
  minutes?: number;
  /** A value from the project's products; left out keeps the current product. */
  product?: string;
  /** After a discount ended unpaid: keep it anyway (goodwill) instead of the full price. */
  keep_discount?: boolean;
}

/** Server defaults, the starting values of a new project. */
export interface AdminDefaultsInterface {
  deposit_ttl_minutes: number;
  remainder_window_minutes: number;
  amount_tolerance_bps: number;
}

export interface DepositFilterInterface {
  skip: number;
  limit: number;
  search?: string;
  project_id?: string | null;
  chain?: string | null;
  /** native coins, or the tokens of one standard (ERC20, BEP-20, TRC20) */
  type?: string | null;
  asset?: string | null;
  status?: InternalStatus | null;
  /** overpaid or underpaid */
  difference?: 'over' | 'under' | null;
}

/** One of a project's products. */
export interface ProductInterface {
  value: string;
  name: string;
  /** A note shown next to the name in product lists only, e.g. "(Most popular)". */
  subname: string | null;
  /** The price in USD, paid in USDT or USDC; null when the amount is entered by hand. */
  price?: string | null;
}

export interface AssetTotalInterface {
  chain: string;
  chain_name: string;
  asset: string;
  decimals: number;
  /** confirmed (credited, not swept yet) or swept */
  status: InternalStatus;
  count: number;
  amount: string;
}

export interface WatcherStateInterface {
  chain: string;
  chain_name: string;
  last_block: number | null;
  head: number | null;
  heartbeat_at: string | null;
}

export interface FunderBalanceInterface {
  chain: string;
  chain_name: string;
  symbol: string;
  decimals: number;
  /** Smallest unit; null when the chain's RPC could not be read. */
  balance: string | null;
  error: string | null;
  address_url: string;
}

export interface FunderBalancesInterface {
  family: string;
  /** m/{project}'/{coin}'/{year}: a new funder each UTC year */
  path: string;
  address: string;
  balances: FunderBalanceInterface[];
}

export interface TreasuryBalanceInterface {
  chain: string;
  chain_name: string;
  asset: string;
  decimals: number;
  /** Smallest unit; null when the chain's RPC could not be read. */
  balance: string | null;
  error: string | null;
  address_url: string;
}

export interface TreasuryInterface {
  family: string;
  /** null until the project sets one */
  address: string | null;
  balances: TreasuryBalanceInterface[];
}

export type SweepStage = 'waiting' | 'topping_up' | 'sweeping' | 'held';

export interface SweeperStateInterface {
  chain: string;
  chain_name: string;
  family: string;
  heartbeat_at: string | null;
}

export interface SweeperInterface {
  counts: Record<SweepStage | 'swept', number>;
  sweepers: SweeperStateInterface[];
  /** A page of the credited deposits not swept yet: held first, then oldest first. */
  queue: DepositInterface[];
  queue_page: number;
  queue_size: number;
  /** A page of the sweeps, newest first; counts.swept is their total. */
  recent: DepositInterface[];
  recent_page: number;
  recent_size: number;
}

export interface StatsInterface {
  total: number;
  by_status: Record<InternalStatus, number>;
  totals: AssetTotalInterface[];
  watchers: WatcherStateInterface[];
  /** how many projects there are */
  projects: number;
}

export interface AssetInterface {
  symbol: string;
  decimals: number;
  /** null for the network's native coin */
  contract: string | null;
  /** ERC20, TRC20 for a token; null for the native coin */
  standard: string | null;
  /** A USD stablecoin (USDT, USDC): what priced products are paid in. */
  usd?: boolean;
}

export interface ChainInterface {
  key: string;
  name: string;
  family: string;
  confirmations: number;
  assets: AssetInterface[];
}

/** A contract and decimals a token had before; deposits opened before `until` keep them. */
export interface TokenChangeInterface {
  contract: string;
  decimals: number;
  until: string;
}

/**
 * supported: in chains.yaml and confirmed by its contract; not supported: taken out of chains.yaml,
 * kept for its deposits; refused: the file's decimals contradict the contract; unchecked: new or
 * changed, its contract not read yet.
 */
export type NetworkAssetStatus = 'supported' | 'not supported' | 'refused' | 'unchecked';

export interface NetworkAssetInterface {
  symbol: string;
  native: boolean;
  contract: string | null;
  decimals: number | null;
  status: NetworkAssetStatus;
  supported: boolean;
  supported_since: string | null;
  supported_until: string | null;
  verified_at: string | null;
  problem: string | null;
  deposits: number;
  history: TokenChangeInterface[];
}

/** A network chains.yaml lists or once listed. */
export interface NetworkInterface {
  key: string;
  name: string;
  family: string;
  chain_id: number | null;
  confirmations: number;
  explorer: string;
  token_standard: string;
  supported: boolean;
  supported_since: string | null;
  supported_until: string | null;
  deposits: number;
  assets: NetworkAssetInterface[];
}

// ---- users and sign-in

/** owner: also deletes, and manages owners; admin: changes things; viewer: only looks. */
export type Role = 'owner' | 'admin' | 'viewer';

export interface UserInterface {
  id: string;
  email: string;
  name: string;
  role: Role;
  /** The projects an admin or viewer sees: null for all of them (owners always see all). */
  projects: string[] | null;
  is_active: boolean;
  created_at: string;
  last_login_at: string | null;
}

export interface TokenPairInterface {
  access_token: string;
  refresh_token: string;
  token_type: string;
  /** Seconds the access token is valid. */
  expires_in: number;
  user: UserInterface;
}

// ---- projects

/** locked: in an admin's or viewer's list, a project an owner has not given them. */
export type ProjectStatus = 'awaiting_keys' | 'active' | 'disabled' | 'locked';

/** What a project is set up with; every field is optional on an update. */
export interface ProjectFieldsInterface {
  name?: string;
  description?: string | null;
  /** http(s) */
  website?: string | null;
  products?: ProductInterface[];
  /** Where deposits are swept, per chain family. */
  treasury?: Record<string, string | null>;
  /** Accepted networks and their assets, e.g. { sepolia: ['ETH', 'USDC'] }. */
  chains?: Record<string, string[]>;
  deposit_ttl_minutes?: number;
  remainder_window_minutes?: number;
  amount_tolerance_bps?: number;
  /** API keys work only from these; empty allows any. */
  allowed_ips?: string[];
}

export interface ProjectInterface {
  id: string;
  name: string;
  description: string | null;
  website: string | null;
  /** Random hardened derivation index: every path starts m/{number}'. */
  number: number;
  status: ProjectStatus;
  /** Family -> m/{number}'/{coin}' */
  key_paths: Record<string, string>;
  xpubs: Record<string, string | null>;
  treasury: Record<string, string | null>;
  chains: Record<string, string[]>;
  products: ProductInterface[];
  deposit_ttl_minutes: number;
  remainder_window_minutes: number;
  amount_tolerance_bps: number;
  allowed_ips: string[];
  events_acked: number;
  created_at: string | null;
  /** The last change: settings, keys or status. */
  updated_at: string | null;
}

export type ApiKeyKind = 'secret' | 'publishable';

export interface ApiKeyInterface {
  id: string;
  kind: ApiKeyKind;
  name: string;
  /** The first characters, to tell keys apart. */
  prefix: string;
  created_at: string;
  last_used_at: string | null;
  revoked_at: string | null;
}

/** A new key: the only time its full value is shown. */
export interface CreatedApiKeyInterface extends ApiKeyInterface {
  key: string;
}

export interface EventInterface {
  id: string;
  seq: number;
  type: string;
  created_at: string;
  deposit: DepositInterface;
}
