// Plain data (no React) so server-rendered landing pages can import it.
// Mirrors trades.TRADES in the API (ids must match; the API falls back to security for unknown ids).
export const TRADES = [
  { id: "security", label: "Security & low-voltage", emoji: "🔐", sells: "alarms, cameras, access control and monitoring" },
  { id: "solar", label: "Solar & energy", emoji: "☀️", sells: "solar, batteries, EV charging and efficiency upgrades" },
  { id: "hvac", label: "HVAC & mechanical", emoji: "❄️", sells: "HVAC install, service contracts and controls" },
  { id: "cleaning", label: "Commercial cleaning", emoji: "🧽", sells: "janitorial, post-construction and recurring cleaning" },
  { id: "it", label: "IT, telecom & internet", emoji: "🌐", sells: "managed IT, internet, phones and structured cabling" },
  { id: "pos", label: "POS & payments", emoji: "💳", sells: "point of sale, card processing and online ordering" },
  { id: "insurance", label: "Business insurance", emoji: "🛡️", sells: "commercial property, liability and workers' comp" },
  { id: "furniture", label: "Office furniture & interiors", emoji: "🪑", sells: "furniture, workspace design and installation" },
] as const;

