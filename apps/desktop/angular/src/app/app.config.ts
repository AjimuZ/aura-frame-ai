// Root application configuration: router and zone change detection.
import { ApplicationConfig, provideZoneChangeDetection } from '@angular/core';
import { provideRouter, withHashLocation } from '@angular/router';
import { routes } from './app.routes';

export const appConfig: ApplicationConfig = {
  providers: [
    provideZoneChangeDetection({ eventCoalescing: true }),
    // Hash location keeps routing working when loaded from file:// in packaged Electron.
    provideRouter(routes, withHashLocation()),
  ],
};
