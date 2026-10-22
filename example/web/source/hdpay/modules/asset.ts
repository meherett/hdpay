import { AssetInterface } from '../services/api.interface';

/** "USDT (ERC20)" for a token, "ETH" for a native coin. */
export function assetLabel(asset: AssetInterface): string {
  return asset.standard ? `${asset.symbol} (${asset.standard})` : asset.symbol;
}
