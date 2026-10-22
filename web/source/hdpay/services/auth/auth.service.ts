import { HttpClient } from '@angular/common/http';
import { Injectable, computed, inject, signal } from '@angular/core';
import { Observable, finalize, map, of, shareReplay, tap } from 'rxjs';

import { environment } from '../../../environments/environment';
import { TokenPairInterface, UserInterface } from '../api.interface';
import { StorageService } from '../storage/storage.service';

export const ACCESS_TOKEN = 'accessToken';
export const REFRESH_TOKEN = 'refreshToken';

/**
 * Admin sign-in. The API returns a short-lived access token and a refresh token; both are kept in
 * localStorage. The interceptor sends the access token and, when it has expired, refreshes it once
 * (the refresh token is replaced each time) before retrying.
 */
@Injectable({ providedIn: 'root' })
export class AuthService {
  private readonly httpClient = inject(HttpClient);
  private readonly storageService = inject(StorageService);
  private readonly endpoint = `${environment.endpoint}/auth`;

  private readonly accessToken = signal<string | null>(this.storageService.getStorage(ACCESS_TOKEN));
  public readonly user = signal<UserInterface | null>(null);
  public readonly isSignedIn = computed(() => this.accessToken() !== null);
  /** Can change things: an admin or an owner. */
  public readonly isAdmin = computed(() => ['owner', 'admin'].includes(this.user()?.role ?? ''));
  /** Can also delete deposit addresses and projects, and manage owners. */
  public readonly isOwner = computed(() => this.user()?.role === 'owner');

  private refreshing: Observable<string> | null = null;

  public getAccessToken(): string | null {
    return this.accessToken();
  }

  public login(email: string, password: string): Observable<UserInterface> {
    return this.httpClient
      .post<TokenPairInterface>(`${this.endpoint}/login`, { email, password })
      .pipe(map((tokens) => this.saveSession(tokens)));
  }

  /** The signed-in user, loaded after a page reload. */
  public me(): Observable<UserInterface> {
    const known = this.user();
    if (known) {
      return of(known);
    }
    return this.httpClient.get<UserInterface>(`${this.endpoint}/me`).pipe(tap((user) => this.user.set(user)));
  }

  /** A new access token; concurrent callers share one request, since a refresh token works once. */
  public refresh(): Observable<string> {
    if (!this.refreshing) {
      const refreshToken = this.storageService.getStorage(REFRESH_TOKEN);
      this.refreshing = this.httpClient
        .post<TokenPairInterface>(`${this.endpoint}/refresh`, { refresh_token: refreshToken ?? '' })
        .pipe(
          map((tokens) => {
            this.saveSession(tokens);
            return tokens.access_token;
          }),
          finalize(() => (this.refreshing = null)),
          shareReplay(1)
        );
    }
    return this.refreshing;
  }

  public changePassword(oldPassword: string, newPassword: string): Observable<void> {
    return this.httpClient.post<void>(`${this.endpoint}/change-password`, {
      old_password: oldPassword,
      new_password: newPassword
    });
  }

  /** Ends this session on the server (best effort) and here (always). */
  public logout(): void {
    const refreshToken = this.storageService.getStorage(REFRESH_TOKEN);
    if (refreshToken) {
      this.httpClient.post(`${this.endpoint}/logout`, { refresh_token: refreshToken }).subscribe({ error: () => undefined });
    }
    this.clearSession();
  }

  public clearSession(): void {
    this.storageService.clearStorage(ACCESS_TOKEN).clearStorage(REFRESH_TOKEN);
    this.accessToken.set(null);
    this.user.set(null);
  }

  private saveSession(tokens: TokenPairInterface): UserInterface {
    this.storageService.setStorage(ACCESS_TOKEN, tokens.access_token).setStorage(REFRESH_TOKEN, tokens.refresh_token);
    this.accessToken.set(tokens.access_token);
    this.user.set(tokens.user);
    return tokens.user;
  }
}
