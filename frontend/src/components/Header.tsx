"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";
import { Layers, Menu, Moon, Sun, X } from "lucide-react";
import { useJobs } from "./Providers";

const LINKS = [
  { href: "/", label: "Convert" },
  { href: "/queue", label: "Queue" },
  { href: "/supported", label: "Supported sources & formats" },
  { href: "/how-it-works", label: "How it works" },
  { href: "/faq", label: "FAQ" },
];

export function ThemeToggle() {
  const [theme, setTheme] = useState<"dark" | "light">("dark");
  useEffect(() => {
    setTheme((document.documentElement.dataset.theme as "dark" | "light") || "dark");
  }, []);
  const toggle = () => {
    const next = theme === "dark" ? "light" : "dark";
    setTheme(next);
    document.documentElement.dataset.theme = next;
    try {
      localStorage.setItem("oc-theme", next);
    } catch {
      /* storage unavailable */
    }
  };
  return (
    <button className="btn btn-ghost btn-icon" onClick={toggle} aria-label={theme === "dark" ? "Switch to light theme" : "Switch to dark theme"} title="Toggle theme">
      {theme === "dark" ? <Sun size={18} aria-hidden /> : <Moon size={18} aria-hidden />}
    </button>
  );
}

export function Header() {
  const pathname = usePathname();
  const [open, setOpen] = useState(false);
  const { jobs } = useJobs();
  const active = jobs.filter((j) => ["queued", "processing", "pending", "analyzing"].includes(j.status)).length;
  useEffect(() => setOpen(false), [pathname]);
  return (
    <header className="site-header">
      <div className="container inner">
        <Link href="/" className="brand" aria-label="OmniConvert home">
          <span className="brand-mark" aria-hidden><Layers size={16} strokeWidth={2.4} /></span>
          OmniConvert
        </Link>
        <button className="btn btn-ghost btn-icon menu-btn" aria-expanded={open} aria-controls="site-nav" aria-label={open ? "Close menu" : "Open menu"} onClick={() => setOpen((o) => !o)}>
          {open ? <X size={20} aria-hidden /> : <Menu size={20} aria-hidden />}
        </button>
        <nav id="site-nav" className={`nav ${open ? "open" : ""}`} aria-label="Main">
          {LINKS.map((l) => (
            <Link key={l.href} href={l.href} aria-current={pathname === l.href ? "page" : undefined}>
              {l.label}
              {l.href === "/queue" && active > 0 && <span className="badge badge-accent" aria-label={`${active} active`}>{active}</span>}
            </Link>
          ))}
        </nav>
        <ThemeToggle />
      </div>
    </header>
  );
}

export function Footer() {
  return (
    <footer className="site-footer">
      <div className="container inner">
        <span>OmniConvert — convert media you own or are authorized to use.</span>
        <nav aria-label="Legal and support">
          <Link href="/privacy">Privacy</Link>
          <Link href="/terms">Terms</Link>
          <Link href="/report">Report abuse or copyright</Link>
          <Link href="/supported">Compatibility</Link>
        </nav>
      </div>
    </footer>
  );
}
