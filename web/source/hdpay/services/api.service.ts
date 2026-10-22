import { HttpClient, HttpHeaders } from '@angular/common/http';
import { Observable } from 'rxjs';

import { environment } from '../../environments/environment';

/**
 * Base wrapper around HttpClient. A subclass passes its resource prefix
 * ('admin', 'chains'), and the specific path arrives as the query argument:
 *
 *   this.adminService.collection(undefined, '/deposits')
 */
export abstract class APIService {
  protected endpoint: string;

  protected constructor(public httpClient: HttpClient, model: string) {
    this.endpoint = `${environment.endpoint}/${model}`;
  }

  public create<T>(data: unknown, headers?: HttpHeaders, query: string = ''): Observable<T> {
    return this.httpClient.post<T>(`${this.endpoint}${query}`, data, { headers });
  }

  public collection<T>(headers?: HttpHeaders, query: string = ''): Observable<T> {
    return this.httpClient.get<T>(`${this.endpoint}${query}`, { headers });
  }

  public get<T>(id: string, headers?: HttpHeaders, query: string = ''): Observable<T> {
    return this.httpClient.get<T>(`${this.endpoint}/${id}${query}`, { headers });
  }

  public update<T>(id: string, data: unknown, headers?: HttpHeaders, query: string = ''): Observable<T> {
    return this.httpClient.patch<T>(`${this.endpoint}/${id}${query}`, data, { headers });
  }

  public delete<T>(id: string, headers?: HttpHeaders, query: string = ''): Observable<T> {
    return this.httpClient.delete<T>(`${this.endpoint}/${id}${query}`, { headers });
  }
}
