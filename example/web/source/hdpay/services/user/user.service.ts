import { HttpClient } from '@angular/common/http';
import { Injectable } from '@angular/core';
import { Observable } from 'rxjs';

import { APIService } from '../api.service';
import { UserInterface } from '../api.interface';

@Injectable({ providedIn: 'root' })
export class UserService extends APIService {
  constructor(httpClient: HttpClient) {
    super(httpClient, 'users');
  }

  /** The shop's users, to pick who is buying: a sample stands in for signing in. */
  public list(): Observable<UserInterface[]> {
    return this.collection<UserInterface[]>();
  }
}
