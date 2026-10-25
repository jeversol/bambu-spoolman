import { CurrentSpool } from "@/components/tray-config/CurrentSpool";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { SpoolConfiguration } from "@/components/tray-config/SpoolConfiguration";
import { Button } from "@/components/ui/button";
import { Link } from "react-router";

function ExternalSpoolConfiguration() {
  return (
    <>
      <Card className="mt-6">
        <CardHeader>
          <CardTitle>External Spool</CardTitle>
        </CardHeader>
        <CardContent>
          <CurrentSpool trayId={255} />
        </CardContent>
      </Card>
      <Card className="mt-6">
        <CardHeader>
          <CardTitle>Select Spool</CardTitle>
        </CardHeader>
        <CardContent>
          <SpoolConfiguration trayId={255} />
          <Button variant="outline" className="mt-4 float-left" asChild>
            <Link to="/">Back</Link>
          </Button>
        </CardContent>
      </Card>
    </>
  );
}

export default function ExternalSpoolPage() {
  return (
    <div className="container mx-auto p-4 max-w-2xl">
      <ExternalSpoolConfiguration />
    </div>
  );
}
