import type { Metadata } from "next";
import { Nav } from "@/components/nav";
import "./globals.css";
import { Providers } from "./providers";

export const metadata: Metadata = {
  title: "Unit economics",
  description: "Revenue, LLM cost and margin per customer and feature",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body className="min-h-screen">
        <Providers>
          <Nav />
          <main className="mx-auto max-w-7xl px-4 py-6 sm:px-6">{children}</main>
        </Providers>
      </body>
    </html>
  );
}
