import type { Metadata } from "next";
import { notFound } from "next/navigation";
import Landing from "@/components/Landing";
import { TRADE_COPY, tradeCopy } from "@/lib/tradeCopy";

export const dynamicParams = false;

export function generateStaticParams() {
  return TRADE_COPY.map((t) => ({ trade: t.id }));
}

export async function generateMetadata({ params }: { params: Promise<{ trade: string }> }): Promise<Metadata> {
  const t = tradeCopy((await params).trade);
  return t
    ? { title: `FirstKnock for ${t.label} — ${t.headline}`, description: t.sub }
    : { title: "FirstKnock" };
}

export default async function TradeLanding({ params }: { params: Promise<{ trade: string }> }) {
  const t = tradeCopy((await params).trade);
  if (!t) notFound();
  return <Landing trade={t} />;
}
