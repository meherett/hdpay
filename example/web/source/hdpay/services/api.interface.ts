/** What the customer is told; the API maps its internal states onto these. */
export type DepositStatus = 'pending' | 'expired' | 'confirming' | 'partial' | 'paid' | 'contact_support';

/** A product discount the deposit opened with; see the admin for what each status means. */
export interface DiscountInterface {
  percent: number;
  ends_at: string;
  /** Base units: the full price, and the discounted one. */
  full_amount: string;
  amount: string;
  status: 'active' | 'kept' | 'lapsed' | 'honored';
}

export interface DepositInterface {
  id: string;
  account_id: string;
  /** What the customer is paying for: the stored value, and the name to show. */
  product: string | null;
  product_name: string | null;
  chain: string;
  chain_name: string;
  asset: string;
  decimals: number;
  address: string;
  address_url: string;
  status: DepositStatus;
  /** Base units as strings; null means any amount is accepted. */
  expected_amount: string | null;
  received_amount: string;
  tx_hash: string | null;
  tx_url: string | null;
  confirmations: number;
  created_at: string;
  expires_at: string;
  expires_in_seconds: number;
  discount: DiscountInterface | null;
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

/** One entry of products.json. */
export interface ProductInterface {
  value: string;
  name: string;
  /** A note shown next to the name in product lists only, e.g. "(Most popular)". */
  subname: string | null;
  /** The price in USD, paid in USDT or USDC; null when the amount is entered by hand. */
  price?: string | null;
}

/** One of the shop's users. */
export interface UserInterface {
  /** HDPay derives the user's deposit addresses from it. */
  account_id: string;
  name: string;
  email: string;
}

/** An order: the shop sends the address to the user's email, and HDPay sets the amount from the price. */
export interface CreateOrderInterface {
  account_id: string;
  product: string;
  chain: string;
  asset: string;
  /** A discount for this customer: a percent off until a moment (ISO, UTC); both or neither. */
  discount_percent?: number | null;
  discount_ends_at?: string | null;
}
