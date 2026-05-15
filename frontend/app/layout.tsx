import type { Metadata } from "next";
import { CopilotKit } from "@copilotkit/react-core";
import { Geist, Geist_Mono } from "next/font/google";
import "@copilotkit/react-ui/v2/styles.css";
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
  title: "DeepMind",
  description: "需求管理智能助手",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html
      lang="zh-CN"
      className={`${geistSans.variable} ${geistMono.variable} h-full`}
    >
      <body className="h-full bg-background text-foreground antialiased">
        <CopilotKit runtimeUrl="/api/copilotkit" agent="deepmind">{children}</CopilotKit>
      </body>
    </html>
  );
}
