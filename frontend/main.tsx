import { Component, type ReactNode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter, Routes, Route } from "react-router";
import { ApplicationProvider } from "./lib/application";
import { ThemeToggle } from "./components/ThemeToggle";
import { NotFound } from "./components/NotFound";
import Home from "./app/page";
import ExternalSpool from "./app/external-spool/page";
import Tray from "./app/ams/[amsId]/tray/[trayId]/page";
import "@fontsource-variable/geist/index.css";
import "@fontsource-variable/geist-mono/index.css";
import "./app/globals.css";

class ErrorBoundary extends Component<
  { children: ReactNode },
  { failed: boolean }
> {
  state = { failed: false };
  static getDerivedStateFromError() {
    return { failed: true };
  }
  render() {
    return this.state.failed ? (
      <main role="alert" className="p-6">
        Something went wrong.{" "}
        <button onClick={() => window.location.reload()}>Reload page</button>
      </main>
    ) : (
      this.props.children
    );
  }
}
createRoot(document.getElementById("root")!).render(
  <ErrorBoundary>
    <BrowserRouter>
      <header className="mx-auto flex w-full max-w-6xl justify-end px-4 pt-4">
        <ThemeToggle />
      </header>
      <ApplicationProvider>
        <Routes>
          <Route path="/" element={<Home />} />
          <Route path="/external-spool" element={<ExternalSpool />} />
          <Route path="/ams/:amsId/tray/:trayId" element={<Tray />} />
          <Route path="*" element={<NotFound />} />
        </Routes>
      </ApplicationProvider>
    </BrowserRouter>
  </ErrorBoundary>,
);
