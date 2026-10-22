import { HttpClient, HttpErrorResponse } from '@angular/common/http';
import { Injectable } from '@angular/core';
import { EMPTY, Observable, catchError, distinctUntilChanged, exhaustMap, filter, timer } from 'rxjs';

import { APIService } from '../api.service';
import { ProductInterface } from '../api.interface';

/** How often the page asks for the products, so a change in the admin shows up without a reload. */
const WATCH_MS = 3000;

@Injectable({ providedIn: 'root' })
export class ProductService extends APIService {
  constructor(httpClient: HttpClient) {
    super(httpClient, 'products');
  }

  /** What customers can pay for: the project's products; empty when it has none. */
  public list(): Observable<ProductInterface[]> {
    return this.collection<ProductInterface[]>();
  }

  /**
   * The products now, then again whenever they change. Polls while the tab is visible; a failed
   * poll goes to onError and is retried on the next tick, so the stream never ends.
   */
  public watch(onError?: (response: HttpErrorResponse) => void): Observable<ProductInterface[]> {
    return timer(0, WATCH_MS).pipe(
      filter((tick) => tick === 0 || document.visibilityState === 'visible'),
      exhaustMap(() =>
        this.list().pipe(
          catchError((response: HttpErrorResponse) => {
            onError?.(response);
            return EMPTY;
          })
        )
      ),
      distinctUntilChanged((previous, next) => JSON.stringify(previous) === JSON.stringify(next))
    );
  }
}
