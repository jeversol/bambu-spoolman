import { headers } from "next/headers";
import { Suspense } from "react";
import { SpoolMappingDashboard } from "@/components/dashboard/SpoolMappingDashboard";
import { Skeleton } from "@/components/ui/skeleton";
import {
  type DashboardTray,
  normalizeColorHex,
  toDashboardSpool,
} from "@/lib/dashboard";
import {
  getPrinterSettings,
  getPrinterTray,
  isPrinterTrayOccupied,
} from "@/lib/printer";
import { getSettings } from "@/lib/settings";
import { getAllSpools } from "@/lib/spool";

const UNDEFINED_RFID_TAG = "00000000000000000000000000000000";

function SkeletonPage() {
  return (
    <div className="space-y-7">
      <Skeleton className="h-12 w-full rounded-xl" />
      <div>
        <Skeleton className="mb-3 h-6 w-20" />
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          {["one", "two", "three", "four"].map((key) => (
            <Skeleton key={key} className="h-80 rounded-2xl" />
          ))}
        </div>
      </div>
    </div>
  );
}

async function HomePage() {
  await headers();

  const [settings, printerSettings, rawSpools] = await Promise.all([
    getSettings(),
    getPrinterSettings(),
    getAllSpools(),
  ]);
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
          : `AMS ${Math.floor(numericTrayId / 4) + 1} · Tray ${(numericTrayId % 4) + 1}`;
      return [Number(spoolId), location];
    }),
  );

  return (
    <SpoolMappingDashboard
      connected={printerSettings !== null}
      trays={trays}
      externalSpool={spoolsById.get(settings.trays[255]) ?? null}
      spools={spools}
      assignments={assignments}
    />
  );
}

export default function Home() {
  return (
    <main className="mx-auto w-full max-w-6xl px-4 pb-16 pt-5">
      <Suspense fallback={<SkeletonPage />}>
        <HomePage />
      </Suspense>
    </main>
  );
}
