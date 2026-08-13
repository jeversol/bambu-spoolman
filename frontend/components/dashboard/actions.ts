"use server";

import { revalidateTag } from "next/cache";
import { grpcClient } from "@/lib/grpc";

export type RfidActionResult = {
  error: string | null;
};

export async function overrideRfidMapping(
  trayId: number,
  uuid: string,
): Promise<RfidActionResult> {
  try {
    await grpcClient.overrideTrayRFID({ trayId, uuid });
    revalidateTag("settings", "max");
    return { error: null };
  } catch (error) {
    return {
      error:
        error instanceof Error
          ? error.message
          : "Could not override RFID mapping",
    };
  }
}

export async function unlinkRfidMapping(
  trayId: number,
  spoolId: number,
  uuid: string,
): Promise<RfidActionResult> {
  try {
    // Suppress automatic re-linking (and auto-creation) while this tag remains
    // physically present, then remove the permanent association.
    await grpcClient.overrideTrayRFID({ trayId, uuid });
    await grpcClient.setTrayUUID({ spoolId, uuid: "" });
    revalidateTag("settings", "max");
    return { error: null };
  } catch (error) {
    return {
      error:
        error instanceof Error ? error.message : "Could not unlink RFID tag",
    };
  }
}
