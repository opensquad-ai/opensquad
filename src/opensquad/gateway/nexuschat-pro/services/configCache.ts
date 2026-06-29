import { systemConfigAPI } from './api';

let cachedConfig: Record<string, any> | null = null;
let inflight: Promise<Record<string, any>> | null = null;

export function peekSystemConfig(): Record<string, any> | null {
  return cachedConfig;
}

export async function getSystemConfigCached(force = false): Promise<Record<string, any>> {
  if (!force && cachedConfig) return cachedConfig;
  if (!force && inflight) return inflight;

  inflight = systemConfigAPI.get()
    .then((config) => {
      cachedConfig = config;
      return config;
    })
    .finally(() => {
      inflight = null;
    });

  return inflight;
}

/** Call after login so settings open instantly later. */
export function preloadSystemConfig(): void {
  void getSystemConfigCached();
}

export function clearSystemConfigCache(): void {
  cachedConfig = null;
}

export function setSystemConfigCache(config: Record<string, any>): void {
  cachedConfig = config;
}
