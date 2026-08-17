import type { Metadata } from "next";
import { ServiceWorkerCleanup } from "@/components/service-worker-cleanup";
import "./globals.css";

export const metadata: Metadata = {
  title: "Выписки → 1С",
  description: "Проверка банковских выписок перед загрузкой в 1С",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="ru">
      <body>
        <ServiceWorkerCleanup />
        {children}
      </body>
    </html>
  );
}
