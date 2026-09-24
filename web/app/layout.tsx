import {
  ClerkProvider,
  OrganizationSwitcher,
  Show,
  SignInButton,
  UserButton,
} from "@clerk/nextjs";
import type { Metadata } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import Link from "next/link";
import "./globals.css";

const geistSans = Geist({ variable: "--font-geist-sans", subsets: ["latin"] });
const geistMono = Geist_Mono({ variable: "--font-geist-mono", subsets: ["latin"] });

export const metadata: Metadata = {
  title: "sliderule",
  description:
    "Source, filter and contact part-time engineers from public permit-filing data.",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="en" className={`${geistSans.variable} ${geistMono.variable} h-full antialiased`}>
      <body>
        <ClerkProvider>
          <div className="sheet">
            <header className="tb">
              <div className="tb-logo">
                slide<em>rule</em>
              </div>
              <nav className="tb-nav">
                <Link href="/campaigns">Campaigns</Link>
                <Link href="/filings">Filings</Link>
                <span className="soon" title="Build step 5">
                  Replies
                </span>
                <span className="soon">Settings</span>
              </nav>
              <div className="tb-org">
                <span className="lbl">Organization</span>
                <Show when="signed-in">
                  <OrganizationSwitcher
                    hidePersonal
                    appearance={{
                      elements: {
                        organizationSwitcherTrigger:
                          "text-[12.5px] font-semibold px-0",
                      },
                    }}
                  />
                </Show>
                <Show when="signed-out">
                  <span style={{ fontSize: 12.5, color: "var(--ink-3)" }}>—</span>
                </Show>
              </div>
              <div>
                <Show when="signed-in">
                  <UserButton />
                </Show>
                <Show when="signed-out">
                  <SignInButton>
                    <button className="stampbtn">Sign in</button>
                  </SignInButton>
                </Show>
              </div>
            </header>
            {children}
          </div>
        </ClerkProvider>
      </body>
    </html>
  );
}
