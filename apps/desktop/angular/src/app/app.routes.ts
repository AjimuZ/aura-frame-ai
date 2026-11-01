// Application routes. Each feature is lazy-loaded as a standalone component.
import { Routes } from '@angular/router';

export const routes: Routes = [
  { path: '', pathMatch: 'full', redirectTo: 'dashboard' },
  {
    path: 'dashboard',
    loadComponent: () =>
      import('./features/dashboard/dashboard.component').then((m) => m.DashboardComponent),
  },
  {
    path: 'import',
    loadComponent: () =>
      import('./features/import-photos/import-photos.component').then(
        (m) => m.ImportPhotosComponent,
      ),
  },
  {
    path: 'analysis',
    loadComponent: () =>
      import('./features/analysis/analysis.component').then((m) => m.AnalysisComponent),
  },
  {
    path: 'review',
    loadComponent: () => import('./features/review/review.component').then((m) => m.ReviewComponent),
  },
  {
    path: 'library-builder',
    loadComponent: () =>
      import('./features/library-builder/library-builder.component').then(
        (m) => m.LibraryBuilderComponent,
      ),
  },
  {
    path: 'reports',
    loadComponent: () =>
      import('./features/reports/reports.component').then((m) => m.ReportsComponent),
  },
  {
    path: 'settings',
    loadComponent: () =>
      import('./features/settings/settings.component').then((m) => m.SettingsComponent),
  },
  { path: '**', redirectTo: 'dashboard' },
];
