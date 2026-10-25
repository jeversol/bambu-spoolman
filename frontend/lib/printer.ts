import type { PrinterStatus, PrinterTray } from "./types";

const UNDEFINED_RFID_TAG = "00000000000000000000000000000000";

export function getPrinterTray(
  settings: PrinterStatus | null,
  tray: number,
): PrinterTray | null {
  if (!settings) return null;

  const amsNum = Math.floor(tray / 4);
  const localTray = tray % 4;
  const amsSettings = settings.print?.ams?.ams?.find(
    (ams) => ams.id === amsNum.toString(),
  );

  return (
    amsSettings?.tray.find(
      (traySettings) => traySettings.id === localTray.toString(),
    ) ?? null
  );
}

export function isPrinterTrayOccupied(
  settings: PrinterStatus | null,
  tray: number,
): boolean | null {
  if (!settings) return null;

  const trayExistBits = settings.print?.ams?.tray_exist_bits;
  if (trayExistBits !== undefined) {
    const bits =
      typeof trayExistBits === "number"
        ? trayExistBits
        : Number.parseInt(trayExistBits, 16);
    if (Number.isFinite(bits)) {
      return (bits & (2 ** tray)) !== 0;
    }
  }

  const printerTray = getPrinterTray(settings, tray);
  if (!printerTray) return null;

  return Boolean(
    printerTray.tray_type ||
      printerTray.tray_sub_brands ||
      printerTray.tray_info_idx ||
      (printerTray.tray_uuid && printerTray.tray_uuid !== UNDEFINED_RFID_TAG),
  );
}
