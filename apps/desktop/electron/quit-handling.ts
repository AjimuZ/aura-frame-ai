// Ties every way out of the app to stopping the analysis services, within a bound, without a quit loop.
// Closing the last window, File > Exit and app.quit() all pass through `before-quit`; `window-all-closed` alone
// misses the last two. Electron does not emit `before-quit` for a system shutdown or log-off on Windows.

// A bound on the time a windowless process may stay alive for Docker. It is long because an `up` in flight must
// end before `down` can run, and a first image build takes minutes: quitting too early during one leaves the
// services running with the photos mounted, which is worse than the wait.
export const QUIT_WAIT_MS = 10 * 60_000;

// The part of Electron's `app` used here, so a test can drive it with a fake.
export interface QuitApp {
  on(event: 'before-quit', listener: (event: { preventDefault(): void }) => void): unknown;
  on(event: 'window-all-closed', listener: () => void): unknown;
  quit(): void;
  exit(code?: number): void;
}

export interface QuitHandlingOptions {
  // Stops the services. Its outcome does not matter here: the app leaves either way.
  dispose: () => Promise<void>;
  // Called once if the bound is reached, just before the app exits. It must tell the person what may remain.
  onTimeout: () => void;
  timeoutMs?: number;
  platform?: NodeJS.Platform;
}

// Resolves to true when `work` settles within `timeoutMs`, and to false otherwise.
function settlesWithin(work: Promise<void>, timeoutMs: number): Promise<boolean> {
  return new Promise((resolve) => {
    const timer = setTimeout(() => resolve(false), timeoutMs);
    const settled = (): void => {
      clearTimeout(timer);
      resolve(true);
    };
    work.then(settled, settled);
  });
}

// Installs the handlers. The first `before-quit` is held until `dispose` settles or the bound is reached, then
// `app.exit` ends the app: it emits no further quit event, so this cannot loop. On macOS the app outlives its
// last window, so closing that window stops the services and nothing else.
export function installQuitHandling(app: QuitApp, options: QuitHandlingOptions): void {
  const timeoutMs = options.timeoutMs ?? QUIT_WAIT_MS;
  const platform = options.platform ?? process.platform;
  let leaving = false;

  app.on('window-all-closed', () => {
    if (platform === 'darwin') {
      void options.dispose();
    } else {
      app.quit();
    }
  });

  app.on('before-quit', (event) => {
    event.preventDefault();
    if (leaving) return;
    leaving = true;
    void settlesWithin(options.dispose(), timeoutMs).then((settled) => {
      if (!settled) options.onTimeout();
      app.exit(0);
    });
  });
}
