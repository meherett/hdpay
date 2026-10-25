import { HttpClient, HttpParams } from '@angular/common/http';
import { Injectable } from '@angular/core';
import { Observable } from 'rxjs';

import { APIService } from '../api.service';
import {
  AdminDefaultsInterface,
  ChainViewInterface,
  ApiKeyInterface,
  ApiKeyKind,
  CreatedApiKeyInterface,
  DepositFilterInterface,
  DepositInterface,
  DepositListInterface,
  EventInterface,
  FunderBalancesInterface,
  NetworkInterface,
  ProjectFieldsInterface,
  ProjectInterface,
  ReopenInterface,
  Role,
  StatsInterface,
  SweeperInterface,
  TreasuryInterface,
  UserInterface
} from '../api.interface';

const id = encodeURIComponent;

/** The API's /admin routes. The interceptor adds the signed-in user's token. */
@Injectable({ providedIn: 'root' })
export class AdminService extends APIService {
  constructor(httpClient: HttpClient) {
    super(httpClient, 'admin');
  }

  public stats(projectId?: string | null): Observable<StatsInterface> {
    return this.collection<StatsInterface>(undefined, projectId ? `/stats?project_id=${id(projectId)}` : '/stats');
  }

  /** Server settings offered as starting values for a new project. */
  public defaults(): Observable<AdminDefaultsInterface> {
    return this.collection<AdminDefaultsInterface>(undefined, '/defaults');
  }

  // ---- deposits

  public deposits(filter: DepositFilterInterface): Observable<DepositListInterface> {
    let params = new HttpParams().set('skip', filter.skip).set('limit', filter.limit);
    for (const key of ['search', 'project_id', 'chain', 'type', 'asset', 'status', 'difference'] as const) {
      const value = filter[key];
      if (value) {
        params = params.set(key, value);
      }
    }
    return this.collection<DepositListInterface>(undefined, `/deposits?${params.toString()}`);
  }

  public deposit(depositId: string): Observable<DepositInterface> {
    return this.collection<DepositInterface>(undefined, `/deposits/${id(depositId)}`);
  }

  /** Sweep queue, recent sweeps and each chain's sweeper check-in. */
  /** Which page of the queue and of Recently swept to list. */
  public sweeper(queue = { page: 1, size: 10 }, recent = { page: 1, size: 10 }): Observable<SweeperInterface> {
    const query = `queue_page=${queue.page}&queue_size=${queue.size}&recent_page=${recent.page}&recent=${recent.size}`;
    return this.collection<SweeperInterface>(undefined, `/sweeper?${query}`);
  }

  /** Support action: let the sweeper retry a held deposit, after the cause was checked. */
  public release(depositId: string): Observable<DepositInterface> {
    return this.create<DepositInterface>({}, undefined, `/deposits/${id(depositId)}/release`);
  }

  /** Back to partial with a fresh window, so the address is watched for the rest again. */
  public reopen(depositId: string, body: ReopenInterface = {}): Observable<DepositInterface> {
    return this.create<DepositInterface>(body, undefined, `/deposits/${id(depositId)}/reopen`);
  }

  /** Support: have the chain's watcher rescan the address and count any payment it missed. */
  public check(depositId: string): Observable<DepositInterface> {
    return this.create<DepositInterface>({}, undefined, `/deposits/${id(depositId)}/check`);
  }

  /** The address's balance now, read from the chain, and when its watcher last ran. */
  public onChain(depositId: string): Observable<ChainViewInterface> {
    return this.collection<ChainViewInterface>(undefined, `/deposits/${id(depositId)}/chain`);
  }

  /** Support action for an underpaid deposit, after a person checked it. */
  public credit(depositId: string): Observable<DepositInterface> {
    return this.create<DepositInterface>({}, undefined, `/deposits/${id(depositId)}/credit`);
  }

  // ---- projects

  public projects(): Observable<ProjectInterface[]> {
    return this.collection<ProjectInterface[]>(undefined, '/projects');
  }

  public project(projectId: string): Observable<ProjectInterface> {
    return this.collection<ProjectInterface>(undefined, `/projects/${id(projectId)}`);
  }

  public createProject(body: ProjectFieldsInterface): Observable<ProjectInterface> {
    return this.create<ProjectInterface>(body, undefined, '/projects');
  }

  public updateProject(projectId: string, body: ProjectFieldsInterface): Observable<ProjectInterface> {
    return this.httpClient.patch<ProjectInterface>(`${this.endpoint}/projects/${id(projectId)}`, body);
  }

  /** The project's xpubs, from `python -m api.keys project <number>` on the master key's host. */
  public setProjectKeys(projectId: string, keys: Record<string, string | null>): Observable<ProjectInterface> {
    return this.create<ProjectInterface>(keys, undefined, `/projects/${id(projectId)}/keys`);
  }

  public setProjectStatus(projectId: string, status: 'active' | 'disabled'): Observable<ProjectInterface> {
    return this.create<ProjectInterface>({}, undefined, `/projects/${id(projectId)}/status?status=${status}`);
  }

  public apiKeys(projectId: string): Observable<ApiKeyInterface[]> {
    return this.collection<ApiKeyInterface[]>(undefined, `/projects/${id(projectId)}/api-keys`);
  }

  /** The reply is the only time the key's full value is available. */
  public createApiKey(projectId: string, kind: ApiKeyKind, name: string): Observable<CreatedApiKeyInterface> {
    return this.create<CreatedApiKeyInterface>({ kind, name }, undefined, `/projects/${id(projectId)}/api-keys`);
  }

  public revokeApiKey(projectId: string, keyId: string): Observable<ApiKeyInterface> {
    return this.httpClient.delete<ApiKeyInterface>(`${this.endpoint}/projects/${id(projectId)}/api-keys/${id(keyId)}`);
  }

  /** The project's latest events, newest first. */
  public projectEvents(projectId: string): Observable<EventInterface[]> {
    return this.collection<EventInterface[]>(undefined, `/projects/${id(projectId)}/events`);
  }

  /** Each treasury with its live balance of every asset the project accepts. Slow: it asks each RPC. */
  public projectTreasury(projectId: string): Observable<TreasuryInterface[]> {
    return this.collection<TreasuryInterface[]>(undefined, `/projects/${id(projectId)}/treasury`);
  }

  /** The project's fee funders with their live balance on each chain. Slow: it asks each RPC. */
  public projectFunders(projectId: string): Observable<FunderBalancesInterface[]> {
    return this.collection<FunderBalancesInterface[]>(undefined, `/projects/${id(projectId)}/funders`);
  }

  /** Every network and coin chains.yaml lists or once listed, supported now or not. */
  public networks(): Observable<NetworkInterface[]> {
    return this.collection<NetworkInterface[]>(undefined, '/networks');
  }

  // ---- users

  public users(): Observable<UserInterface[]> {
    return this.collection<UserInterface[]>(undefined, '/users');
  }

  public createUser(body: {
    email: string;
    name: string;
    password: string;
    role: Role;
    projects: string[] | null;
  }): Observable<UserInterface> {
    return this.create<UserInterface>(body, undefined, '/users');
  }

  /** projects: 'all', or the ids an admin or viewer sees. */
  public updateUser(
    userId: string,
    body: { name?: string; role?: Role; is_active?: boolean; projects?: 'all' | string[] }
  ): Observable<UserInterface> {
    return this.httpClient.patch<UserInterface>(`${this.endpoint}/users/${id(userId)}`, body);
  }

  /** Sets someone's password; their sessions end. */
  public setUserPassword(userId: string, password: string): Observable<void> {
    return this.create<void>({ password }, undefined, `/users/${id(userId)}/password`);
  }

  /** Owners only: the address is no longer watched or swept. */
  public deleteDeposit(depositId: string): Observable<void> {
    return this.httpClient.delete<void>(`${this.endpoint}/deposits/${id(depositId)}`);
  }

  /** Owners only, confirmed with the project's exact name: the project, its keys, deposits and events. */
  public deleteProject(projectId: string, confirm: string): Observable<void> {
    return this.httpClient.delete<void>(`${this.endpoint}/projects/${id(projectId)}`, { params: { confirm } });
  }

  public deleteUser(userId: string): Observable<void> {
    return this.httpClient.delete<void>(`${this.endpoint}/users/${id(userId)}`);
  }
}
