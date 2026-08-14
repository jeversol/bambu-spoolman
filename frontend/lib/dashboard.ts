import type { Spool } from "@/lib/proto/bambu_spoolman/grpc/spoolman";

export type DashboardSpool = {
  id: number;
  name: string;
  vendor: string | null;
  material: string | null;
  colorHex: string | null;
  remainingWeight: number;
  remainingPercent: number | null;
};

export type DashboardTray = {
  id: number;
  amsNumber: number;
  trayNumber: number;
  occupied: boolean | null;
  printerName: string | null;
  printerColorHex: string | null;
  rfidTag: string | null;
  locked: boolean;
  spool: DashboardSpool | null;
};

export function normalizeColorHex(value: string | undefined): string | null {
  if (!value) return null;

  const normalized = value.replace(/^#/, "").slice(0, 6);
  return /^[0-9a-f]{6}$/i.test(normalized) ? `#${normalized}` : null;
}

export function getRemainingPercent(spool: Spool): number | null {
  const totalLength = spool.remainingLength + spool.usedLength;
  if (Number.isFinite(totalLength) && totalLength > 0) {
    return Math.min(
      100,
      Math.max(0, (spool.remainingLength / totalLength) * 100),
    );
  }

  if (
    Number.isFinite(spool.initialWeight) &&
    spool.initialWeight > 0 &&
    Number.isFinite(spool.remainingWeight)
  ) {
    return Math.min(
      100,
      Math.max(0, (spool.remainingWeight / spool.initialWeight) * 100),
    );
  }

  return null;
}

export function toDashboardSpool(spool: Spool): DashboardSpool {
  const filament = spool.filament;
  const fallbackName = filament?.material || `Spool #${spool.id}`;

  return {
    id: Number(spool.id),
    name: filament?.name || fallbackName,
    vendor: filament?.vendor?.name || null,
    material: filament?.material || null,
    colorHex: normalizeColorHex(filament?.colorHex),
    remainingWeight: spool.remainingWeight,
    remainingPercent: getRemainingPercent(spool),
  };
}
