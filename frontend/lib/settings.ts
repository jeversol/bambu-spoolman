import { cacheLife, cacheTag, revalidateTag } from "next/cache";
import { backendRequest } from "./server/api";
import { getSpool } from "./spool";

export type Settings = {
  trays: Record<string, number>;
  trayCount: number;
  lockedTrays: number[];
};

export async function revalidateSettings() {
  "use server";
  revalidateTag("settings", "max");
}

export async function getSettings() {
  "use cache";
  cacheLife("seconds");
  cacheTag("settings");

  const response = await backendRequest<Settings>("/settings");
  return response;
}

/**
 * Gets the spool in a tray
 * @param tray The tray to get
 * @returns The Spool in a tray, if any
 */
export async function getSpoolInTray(tray: number) {
  const settings = await getSettings();

  const spoolId = settings.trays[tray];
  if (spoolId === undefined || spoolId === null) {
    return null;
  }
  return getSpool(spoolId.toString());
}

export async function isLocked(tray: number) {
  const settings = await getSettings();
  return settings.lockedTrays.includes(tray);
}
