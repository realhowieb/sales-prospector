import type { Metadata, Viewport } from "next";
import AuthGate from "@/components/AuthGate";
import Nav from "@/components/Nav";
import "./globals.css";

export const metadata: Metadata = {
  title: "FirstKnock",
  description: "Find businesses that are opening, moving or changing hands — before anyone else knocks.",
  icons: { icon: "data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'><text y='.9em' font-size='90'>🚪</text></svg>" },
};

export const viewport: Viewport = { width: "device-width", initialScale: 1 };

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>
        <AuthGate>
          <Nav />
          <main>{children}</main>
        </AuthGate>
      </body>
    </html>
  );
}
