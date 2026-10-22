import { HttpClient, HttpErrorResponse } from '@angular/common/http';
import { Injectable } from '@angular/core';
import { EMPTY, Observable, catchError, distinctUntilChanged, exhaustMap, filter, timer } from 'rxjs';

import { APIService } from '../api.service';
import { ChainInterface } from '../api.interface';

/** How often open pages ask for the chains, so an edit to the chains file shows up without a reload. */
const WATCH_MS = 3000;

@Injectable({ providedIn: 'root' })
export class ChainService extends APIService {
  constructor(httpClient: HttpClient) {
    super(httpClient, 'chains');
  }

  /** Networks and assets the project accepts. */
  public list(): Observable<ChainInterface[]> {
    return this.collection<ChainInterface[]>();
  }

  /**
   * The chains now, then again whenever they change. Polls while the tab is visible; a failed
   * poll goes to onError and is retried on the next tick, so the stream never ends.
   */
  public watch(onError?: (response: HttpErrorResponse) => void): Observable<ChainInterface[]> {
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
