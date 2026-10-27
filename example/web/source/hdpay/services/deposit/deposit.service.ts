import { HttpClient } from '@angular/common/http';
import { Injectable } from '@angular/core';
import { Observable } from 'rxjs';

import { APIService } from '../api.service';
import { CreateOrderInterface, DepositInterface } from '../api.interface';

/** Orders at the example shop's API; it opens the deposits at HDPay and emails the customer. */
@Injectable({ providedIn: 'root' })
export class DepositService extends APIService {
  constructor(httpClient: HttpClient) {
    super(httpClient, 'orders');
  }

  /** A fresh address for this account, or its open one for the network, asset and product. */
  public open(body: CreateOrderInterface): Observable<DepositInterface> {
    return this.create<DepositInterface>(body);
  }

  /** The order's payment; only orders placed at this shop are found. */
  public find(id: string): Observable<DepositInterface> {
    return this.get<DepositInterface>(encodeURIComponent(id));
  }

  /**
   * More time for the rest of a short payment whose time ran out: back to partial, same address. With
   * `product`, for that product instead, what was sent counting toward its price.
   */
  public reopen(id: string, product?: string): Observable<DepositInterface> {
    return this.create<DepositInterface>(product ? { product } : {}, undefined, `/${encodeURIComponent(id)}/reopen`);
  }

  /** The account's short payments whose time for the rest ran out, to finish or change. */
  public unfinished(accountId: string): Observable<DepositInterface[]> {
    return this.collection<DepositInterface[]>(undefined, `/unfinished?account_id=${encodeURIComponent(accountId)}`);
  }

  /** The customer gives up on a pending address, to pay another way; anything sent to it still counts. */
  public cancel(id: string): Observable<DepositInterface> {
    return this.create<DepositInterface>({}, undefined, `/${encodeURIComponent(id)}/cancel`);
  }

  /** A new address for an expired order: same account, product, network, asset and email. */
  public renew(id: string): Observable<DepositInterface> {
    return this.create<DepositInterface>({}, undefined, `/${encodeURIComponent(id)}/renew`);
  }
}
