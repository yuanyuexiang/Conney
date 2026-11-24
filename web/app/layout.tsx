import { AntdRegistry } from "@ant-design/nextjs-registry";
import type { Metadata } from "next";
import type { ReactNode } from "react";
import "./styles.css";
import "./workbench.css";
import "./delivery.css";

export const metadata: Metadata = {
  title: "康尼新能源 | EDI 交付计划管理",
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="zh-CN">
      <body>
        <AntdRegistry>{children}</AntdRegistry>
      </body>
    </html>
  );
}
