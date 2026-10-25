import { TrayConfigForm } from "@/components/tray-config/TrayConfigForm";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { useApplicationData } from "@/lib/application";
import { AlertCircle } from "lucide-react";

type Props = {
  trayId: number;
};

export function SpoolConfiguration(props: Props) {
  const { settings, spools: allSpools } = useApplicationData();
  const spool =
    allSpools.find((value) => value.id === settings.trays[props.trayId]) ??
    null;
  const selectedSpools = Object.values(settings.trays);
  if (settings.lockedTrays.includes(props.trayId)) {
    return (
      <Alert>
        <AlertCircle />
        <AlertDescription>
          The spool in this slot is automatically selected and cannot be
          changed.
        </AlertDescription>
      </Alert>
    );
  }

  return (
    <TrayConfigForm
      key={spool?.id}
      spool={spool}
      allSpools={allSpools}
      trayId={props.trayId}
      selectedSpools={selectedSpools}
    />
  );
}
