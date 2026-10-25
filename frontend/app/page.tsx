import { SpoolMappingDashboard } from "@/components/dashboard/SpoolMappingDashboard";
import {
  type DashboardTray,
  normalizeColorHex,
  toDashboardSpool,
} from "@/lib/dashboard";
import { getPrinterTray, isPrinterTrayOccupied } from "@/lib/printer";
import { useApplicationData } from "@/lib/application";

const UNDEFINED_RFID_TAG = "00000000000000000000000000000000";

export default function Home() {
  const {
    settings,
    printerSettings,
    spools: rawSpools,
    rfidEnabled,
  } = useApplicationData();
  const spools = rawSpools.map(toDashboardSpool);
  const spoolsById = new Map(spools.map((spool) => [spool.id, spool]));
  const assignedTrayIds = Object.keys(settings.trays)
    .map(Number)
    .filter((trayId) => Number.isFinite(trayId) && trayId !== 255);
  const printerTrayCount = (printerSettings?.print?.ams?.ams?.length ?? 0) * 4;
  const configuredTrayCount =
    assignedTrayIds.length === 0 ? 0 : Math.max(...assignedTrayIds) + 1;
  const trayCount = Math.max(
    settings.trayCount,
    printerTrayCount,
    configuredTrayCount,
  );
  const roundedTrayCount = Math.ceil(trayCount / 4) * 4;

  const trays: DashboardTray[] = Array.from(
    { length: roundedTrayCount },
    (_, id) => {
      const printerTray = getPrinterTray(printerSettings, id);
      const rfidTag = printerTray?.tray_uuid;
      const printerName =
        printerTray?.tray_sub_brands || printerTray?.tray_type || null;

      return {
        id,
        amsNumber: Math.floor(id / 4) + 1,
        trayNumber: (id % 4) + 1,
        occupied: isPrinterTrayOccupied(printerSettings, id),
        printerName,
        printerColorHex: normalizeColorHex(printerTray?.tray_color),
        rfidTag: rfidTag && rfidTag !== UNDEFINED_RFID_TAG ? rfidTag : null,
        locked: settings.lockedTrays.includes(id),
        spool: spoolsById.get(settings.trays[id]) ?? null,
      };
    },
  );

  const assignments = Object.fromEntries(
    Object.entries(settings.trays).map(([trayId, spoolId]) => {
      const numericTrayId = Number(trayId);
      const location =
        numericTrayId === 255
          ? "external holder"
          : `AMS ${Math.floor(numericTrayId / 4) + 1} · Slot ${(numericTrayId % 4) + 1}`;
      return [Number(spoolId), location];
    }),
  );

  return (
    <main className="mx-auto w-full max-w-6xl px-4 pb-16 pt-5">
      <SpoolMappingDashboard
        connected={printerSettings !== null}
        rfidEnabled={rfidEnabled}
        trays={trays}
        externalSpool={spoolsById.get(settings.trays[255]) ?? null}
        spools={spools}
        assignments={assignments}
      />
    </main>
  );
}
