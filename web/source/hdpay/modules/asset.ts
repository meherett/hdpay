import { DestroyRef, Signal, computed, signal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { AbstractControl, FormGroup } from '@angular/forms';

import { AssetInterface, ChainInterface } from '../services/api.interface';

/** A network's own coin (ETH, BNB, TRX) or one of its tokens (USDT, USDC). */
export type AssetType = 'native' | 'token';

export interface AssetPicker {
  /** The chosen network, if it is still offered. */
  chain: Signal<ChainInterface | undefined>;
  native: Signal<AssetInterface | undefined>;
  tokens: Signal<AssetInterface[]>;
  /** What the asset select lists: the native coin, or the tokens. */
  options: Signal<AssetInterface[]>;
  /** Call when the chains change, so choices that no longer exist are cleared. */
  sync(): void;
}

/** "USDT (ERC20)" for a token, "ETH" for a native coin. */
export function assetLabel(asset: AssetInterface): string {
  return asset.standard ? `${asset.symbol} (${asset.standard})` : asset.symbol;
}

/**
 * Network, then type, then asset, on a form with `chain`, `type` and `asset` controls.
 * A native type picks the network's coin by itself and locks the asset select; a token type
 * lists the network's tokens. Read the form with getRawValue(), since a locked control is
 * left out of value.
 */
export function assetPicker(form: FormGroup, chains: Signal<ChainInterface[]>, destroyRef: DestroyRef): AssetPicker {
  const chainControl = form.controls['chain'] as AbstractControl<string | null>;
  const typeControl = form.controls['type'] as AbstractControl<AssetType | null>;
  const assetControl = form.controls['asset'] as AbstractControl<string | null>;

  const chainKey = signal<string | null>(chainControl.value);
  const type = signal<AssetType | null>(typeControl.value);
  const chain = computed(() => chains().find((c) => c.key === chainKey()));
  const native = computed(() => chain()?.assets.find((a) => a.contract === null));
  const tokens = computed(() => chain()?.assets.filter((a) => a.contract !== null) ?? []);
  const options = computed(() => {
    if (type() === 'native') {
      const coin = native();
      return coin ? [coin] : [];
    }
    return type() === 'token' ? tokens() : [];
  });

  const sync = (): void => {
    if (chainControl.value !== null && !chain()) {
      chainControl.reset();   // the network was removed; its valueChanges clears type and asset
      return;
    }
    // a type means nothing until there is a network
    if (chain()) {
      typeControl.enable({ emitEvent: false });
    } else {
      typeControl.disable({ emitEvent: false });
    }
    if (type() === 'token' && !tokens().length) {
      typeControl.reset();    // the network has no tokens any more
      return;
    }
    if (type() === 'native') {
      assetControl.setValue(native()?.symbol ?? null, { emitEvent: false });
      assetControl.disable({ emitEvent: false });
    } else if (type() === 'token') {
      assetControl.enable({ emitEvent: false });
      if (!tokens().some((a) => a.symbol === assetControl.value)) {
        assetControl.reset(null, { emitEvent: false });
      }
    } else {
      assetControl.reset(null, { emitEvent: false });
      assetControl.disable({ emitEvent: false });
    }
  };

  chainControl.valueChanges.pipe(takeUntilDestroyed(destroyRef)).subscribe((key) => {
    chainKey.set(key);
    typeControl.reset();
  });
  typeControl.valueChanges.pipe(takeUntilDestroyed(destroyRef)).subscribe((value) => {
    type.set(value);
    sync();
  });
  sync();

  return { chain, native, tokens, options, sync };
}
