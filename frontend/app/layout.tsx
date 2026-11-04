import type { Metadata } from "next";
import "./globals.css";
import { Providers } from "./providers";
import { ThemeProvider } from "@/components/theme-provider";

export const metadata: Metadata = {
  title: "AI Video Repurposer",
  description: "Turn long-form video into short-form content, powered by AI.",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <html lang="en"><body><ThemeProvider><Providers>{children}</Providers></ThemeProvider></body></html>;
}
