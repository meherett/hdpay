import { Injectable } from '@angular/core';

/**
 * Fluent localStorage wrapper. Every setter returns the service so calls chain.
 * Blocked storage (private browsing) is tolerated: values just do not persist.
 */
@Injectable({ providedIn: 'root' })
export class StorageService {

  public getStorage(key: string): string | null {
    try {
      return localStorage.getItem(key);
    } catch {
      return null;
    }
  }

  public setStorage(key: string, value: string | boolean | number): StorageService {
    try {
      localStorage.setItem(key, value.toString());
    } catch {
      // Private browsing or blocked storage: carry on without persisting.
    }
    return this;
  }

  public clearStorage(key: string): StorageService {
    try {
      localStorage.removeItem(key);
    } catch {
      // Nothing to do.
    }
    return this;
  }
}
