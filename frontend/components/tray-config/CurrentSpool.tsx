import { SpoolChip } from "@/components/SpoolChip";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { useApplicationData } from "@/lib/application";
import { AlertCircle } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { ClearButton } from "./ClearButton";

type Props = {
  trayId: number;
  showClearButton?: boolean;
};

export function CurrentSpool(props: Props) {
  const { settings, spools } = useApplicationData();
  const spoolId = settings.trays[props.trayId];
  if (!spoolId) {
    return (
      <Alert>
        <AlertCircle />
        <AlertDescription>No spool assigned to this slot</AlertDescription>
      </Alert>
    );
  }
  const spool = spools.find((value) => value.id === spoolId);
  const locked = settings.lockedTrays.includes(props.trayId);
  if (!spool) {
    return (
      <Alert variant="destructive">
        <AlertCircle />
        <AlertDescription>
          Spool {spoolId} not found in Spoolman
        </AlertDescription>
      </Alert>
    );
  }

  let percentRemaining =
    (spool.remainingLength / (spool.remainingLength + spool.usedLength)) * 100;
  if (isNaN(percentRemaining)) {
    percentRemaining = 0;
  }

  const showClearButton = props.showClearButton ?? true;

  return (
    <>
      <div className="flex items-center gap-4">
        <SpoolChip spool={spool} size="large" withPercentage />
        <div className="font-medium">
          {spool.filament?.material || "Unknown"}
        </div>
        <div className="text-sm text-muted-foreground">
          {spool.remainingWeight.toFixed(0)}g remaining (
          {percentRemaining.toFixed(1)}%)
        </div>
      </div>

      {locked && <Badge variant="destructive">Locked</Badge>}
      <div className="float-right">
        {showClearButton && (
          <ClearButton trayId={props.trayId} locked={locked} />
        )}
      </div>
    </>
  );
}
