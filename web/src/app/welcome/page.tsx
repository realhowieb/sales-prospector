import type { Metadata } from "next";
import Landing from "@/components/Landing";

export const metadata: Metadata = {
  title: "FirstKnock — be the first to knock on new businesses",
  description:
    "Leads from building permits, liquor licenses, local news and the map — businesses opening, moving and building out, ranked for what you sell.",
};

export default function Welcome() {
  return <Landing />;
}
