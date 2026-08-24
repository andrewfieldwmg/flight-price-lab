import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Flight Price Lab",
  description: "Compare nonstop flights with feasible separate-ticket alternatives.",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
