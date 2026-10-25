import { apiRequest } from "../api.ts";

export type RfidActionResult = { error: string | null };

async function mutate(
  path: string,
  method: "PUT" | "POST",
  body: Record<string, unknown>,
): Promise<RfidActionResult> {
  try {
    await apiRequest<void>(path, {
      method,
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    return { error: null };
  } catch (error) {
    return {
      error: error instanceof Error ? error.message : "RFID operation failed",
    };
  }
}

export function linkRfidMapping(
  spoolId: number,
  uuid: string,
): Promise<RfidActionResult> {
  return mutate(`/spools/${spoolId}/rfid`, "PUT", { uuid });
}

export function overrideRfidMapping(
  trayId: number,
  uuid: string,
): Promise<RfidActionResult> {
  return mutate(`/trays/${trayId}/rfid/override`, "POST", { uuid });
}

export function unlinkRfidMapping(
  trayId: number,
  spoolId: number,
  uuid: string,
): Promise<RfidActionResult> {
  return mutate(`/trays/${trayId}/rfid/unlink`, "POST", { spoolId, uuid });
}
