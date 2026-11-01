import { ipcMain } from 'electron';
import type { ProfileState } from './contracts';
import type { ProfileRegistryStore } from './profile-store';

function assertIdle(isAnalysisRunning: () => boolean): void {
  if (isAnalysisRunning()) {
    throw new Error('Profile management is unavailable while an analysis is running.');
  }
}

export function registerProfilesIpc(
  profiles: ProfileRegistryStore,
  isAnalysisRunning: () => boolean,
): void {
  ipcMain.handle('profiles:get', (): ProfileState => profiles.getState());

  ipcMain.handle('profiles:create', (_event, name: string): ProfileState => {
    assertIdle(isAnalysisRunning);
    return profiles.createProfile(name);
  });

  ipcMain.handle('profiles:rename', (_event, profileId: string, name: string): ProfileState => {
    assertIdle(isAnalysisRunning);
    return profiles.renameProfile(profileId, name);
  });

  ipcMain.handle('profiles:activate', (_event, profileId: string): ProfileState => {
    assertIdle(isAnalysisRunning);
    return profiles.activateProfile(profileId);
  });

  ipcMain.handle('profiles:delete', (_event, profileId: string): ProfileState => {
    assertIdle(isAnalysisRunning);
    return profiles.deleteProfile(profileId);
  });
}
