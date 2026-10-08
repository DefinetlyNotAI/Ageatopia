import type { Metadata } from "next";
import "./globals.css";
export const metadata: Metadata = { title: "Agent Utopia · Observatory", description: "Local agent research observatory" };
export default function Layout({ children }: { children: React.ReactNode }) {
  return <html lang="en"><body>{children}</body></html>;
}
