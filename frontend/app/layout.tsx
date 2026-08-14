import type { Metadata } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import Script from "next/script";
import { ThemeToggle } from "@/components/ThemeToggle";
import "./globals.css";

const geistSans = Geist({
  variable: "--font-geist-sans",
  subsets: ["latin"],
});

const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
});

export const metadata: Metadata = {
  title: "Spool Mapping",
  description: "Map printer trays to filament inventory",
};

const themeScript = `
  (() => {
    const storageKey = "bambu-spoolman-theme";
    let savedTheme = null;

    try {
      savedTheme = localStorage.getItem(storageKey);
    } catch (_) {}

    const theme = savedTheme === "light" ? "light" : "dark";

    document.documentElement.classList.toggle("dark", theme === "dark");
    document.documentElement.style.colorScheme = theme;
  })();
`;

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en" className="dark" suppressHydrationWarning>
      <body
        className={`${geistSans.variable} ${geistMono.variable} min-h-screen antialiased`}
      >
        <Script id="theme-initializer" strategy="beforeInteractive">
          {themeScript}
        </Script>
        <header className="mx-auto flex w-full max-w-6xl justify-end px-4 pt-4">
          <ThemeToggle />
        </header>
        {children}
      </body>
    </html>
  );
}
