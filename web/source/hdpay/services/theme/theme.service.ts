import { Injectable, signal } from '@angular/core';

export type Theme = 'light' | 'dark';

const STORAGE_KEY = 'theme';
const LINK_ID = 'dark-theme';

/**
 * Light or dark admin. ng-zorro's dark stylesheet is a separate bundle (dark.css) loaded only
 * when needed; our own colours are CSS variables keyed on <html data-theme>. index.html applies
 * the saved theme before Angular starts; this service keeps it in sync afterwards.
 */
@Injectable({ providedIn: 'root' })
export class ThemeService {
  public readonly theme = signal<Theme>(document.documentElement.getAttribute('data-theme') === 'dark' ? 'dark' : 'light');

  public toggle(): void {
    this.set(this.theme() === 'dark' ? 'light' : 'dark');
  }

  public set(theme: Theme): void {
    try {
      localStorage.setItem(STORAGE_KEY, theme);
    } catch {
      // Blocked storage: the choice lasts for this page only.
    }

    const link = document.getElementById(LINK_ID) as HTMLLinkElement | null;
    if (theme === 'light') {
      link?.remove();
      document.getElementById('dark-theme-early')?.remove();
      this.apply('light');
    } else if (link) {
      this.apply('dark');
    } else {
      // switch only once ng-zorro's dark styles are in, so the page is never half light, half dark
      const sheet = document.createElement('link');
      sheet.id = LINK_ID;
      sheet.rel = 'stylesheet';
      sheet.href = 'dark.css';
      sheet.onload = () => this.apply('dark');
      sheet.onerror = () => sheet.remove();
      document.head.appendChild(sheet);
    }
  }

  private apply(theme: Theme): void {
    document.documentElement.setAttribute('data-theme', theme);
    this.theme.set(theme);
  }
}
