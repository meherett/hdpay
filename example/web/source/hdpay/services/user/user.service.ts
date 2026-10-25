import { HttpClient } from '@angular/common/http';
import { Injectable } from '@angular/core';
import { Observable, catchError, of } from 'rxjs';

import { APIService } from '../api.service';
import { UserInterface } from '../api.interface';

@Injectable({ providedIn: 'root' })
export class UserService extends APIService {
  constructor(httpClient: HttpClient) {
    super(httpClient, 'users');
  }

  /** Buyers from public/users.json, to try a checkout quickly; none when the file is missing. */
  public fromFile(): Observable<UserInterface[]> {
    return this.httpClient.get<UserInterface[]>('users.json').pipe(catchError(() => of([])));
  }

  /** The shop's users, to pick who is buying: a sample stands in for signing in. */
  public list(): Observable<UserInterface[]> {
    return this.collection<UserInterface[]>();
  }
}
