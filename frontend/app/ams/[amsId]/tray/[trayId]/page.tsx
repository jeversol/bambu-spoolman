import {
  Breadcrumb,
  BreadcrumbItem,
  BreadcrumbList,
  BreadcrumbSeparator,
} from "@/components/ui/breadcrumb";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { useParams, Link } from "react-router";
import { useApplicationData } from "@/lib/application";
import { NotFound } from "@/components/NotFound";
import { CurrentSpool } from "../../../../../components/tray-config/CurrentSpool";
import { SpoolConfiguration } from "@/components/tray-config/SpoolConfiguration";
import { Button } from "@/components/ui/button";
import { getPrinterTray } from "@/lib/printer";
import { RfidLinkButton } from "@/components/tray-config/RfidLinkButton";

type RfidProps = {
  trayId: number;
};

function RfidSettings({ trayId }: RfidProps) {
  const { settings, printerSettings, spools } = useApplicationData();
  const rfidTag = getPrinterTray(printerSettings, trayId)?.tray_uuid;
  const locked = settings.lockedTrays.includes(trayId);
  const spool = spools.find((value) => value.id === settings.trays[trayId]);

  if (!rfidTag || /^0+$/.test(rfidTag) || locked || !spool) {
    return null;
  }

  return (
    <Card className="mt-6">
      <CardHeader>
        <CardTitle>RFID Tag</CardTitle>
      </CardHeader>
      <CardContent>
        <p className="text-foreground">
          A spool with an RFID tag is detected. If you want to automatically
          assign this spool to the slot when it&apos;s inserted, click the
          button below.
        </p>
        <div className="mt-2 block">
          RFID Tag: <pre className="font-mono inline">{rfidTag}</pre>
        </div>
        <RfidLinkButton spoolId={spool.id} uuid={rfidTag} />
      </CardContent>
    </Card>
  );
}

export default function TrayPage() {
  const params = useParams();
  const { rfidEnabled: trayLockingSupported } = useApplicationData();
  const amsId = Number(params.amsId) - 1;
  const rawTrayId = Number(params.trayId) - 1; // Convert to 0-indexed

  const trayId = amsId * 4 + rawTrayId;

  if (
    !Number.isSafeInteger(amsId) ||
    !Number.isInteger(rawTrayId) ||
    amsId < 0 ||
    rawTrayId < 0 ||
    rawTrayId > 3
  ) {
    return <NotFound />;
  }

  return (
    <div className="container mx-auto p-4 max-w-2xl">
      <Breadcrumb>
        <BreadcrumbList>
          <BreadcrumbItem>Home</BreadcrumbItem>
          <BreadcrumbSeparator />
          <BreadcrumbItem>AMS {amsId + 1}</BreadcrumbItem>
          <BreadcrumbSeparator />
          <BreadcrumbItem>Slot {(rawTrayId % 4) + 1}</BreadcrumbItem>
        </BreadcrumbList>
      </Breadcrumb>
      <Card className="mt-6">
        <CardHeader>
          <CardTitle>Current Spool</CardTitle>
        </CardHeader>
        <CardContent>
          <CurrentSpool trayId={trayId} />
        </CardContent>
      </Card>
      {trayLockingSupported && <RfidSettings trayId={trayId} />}
      <Card className="mt-6">
        <CardHeader>
          <CardTitle>Select Spool</CardTitle>
        </CardHeader>
        <CardContent>
          <SpoolConfiguration trayId={trayId} />
          <Button variant="outline" className="mt-4 float-left" asChild>
            <Link to="/">Back</Link>
          </Button>
        </CardContent>
      </Card>
    </div>
  );
}
