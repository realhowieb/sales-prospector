// Marketing copy for the public landing pages, one block per trade.
import { TRADES } from "./tradeData";

export type TradeCopy = {
  id: string;
  label: string;
  emoji: string;
  headline: string;
  sub: string;
  bestLeads: string[];
  pitches: { lead: string; say: string }[];
};

const COPY: Record<string, Omit<TradeCopy, "id" | "label" | "emoji">> = {
  security: {
    headline: "Be the first security rep through the door.",
    sub: "Restaurants pulling liquor licenses, offices building out new floors, shops signing leases — every one needs alarms, cameras and access control before opening day.",
    bestLeads: ["Pending liquor licenses (new restaurants, bars, markets)", "Office and lab build-outs before move-in", "New retail and medical tenants"],
    pitches: [
      { lead: "New restaurant, liquor license pending", say: "CCTV, burglar alarm and after-hours monitoring before you open." },
      { lead: "Law firm building out a new office", say: "Card access, cameras at the entries and an alarm — wired while the walls are open." },
    ],
  },
  solar: {
    headline: "Find solar projects while the plans are still on the table.",
    sub: "New commercial buildings, big renovations and companies moving into labs and warehouses — the moment to put solar, storage and EV charging into the budget.",
    bestLeads: ["New commercial construction", "Lab, R&D and manufacturing build-outs", "Major owner improvements"],
    pitches: [
      { lead: "R&D lab build-out", say: "Solar plus storage to offset heavy equipment load." },
      { lead: "New office building", say: "Rooftop solar and EV chargers for staff, designed in from day one." },
    ],
  },
  hvac: {
    headline: "Win the HVAC contract before the tenant moves in.",
    sub: "Tenant improvements, new kitchens and lab build-outs all mean new mechanical work — and a service contract waiting to be signed.",
    bestLeads: ["Tenant improvements with mechanical scope", "New restaurants and commercial kitchens", "Labs and clinics with air-quality needs"],
    pitches: [
      { lead: "New restaurant build-out", say: "Kitchen ventilation and a maintenance contract from opening day." },
      { lead: "Clinic moving into a new suite", say: "Air quality and a preventive maintenance plan." },
    ],
  },
  cleaning: {
    headline: "Land the cleaning contract on move-in week.",
    sub: "Every company moving into a new office needs a post-construction clean — then someone to clean it every night. Find them before they ask the landlord.",
    bestLeads: ["Companies moving into new offices", "Medical and dental clinics opening", "Gyms and studios building out"],
    pitches: [
      { lead: "Tech company moving into a new floor", say: "Post-construction clean, then nightly janitorial." },
      { lead: "Dental office opening", say: "Medical-grade disinfection on a schedule that fits patient hours." },
    ],
  },
  it: {
    headline: "Wire the new office before the lease starts.",
    sub: "Moves and build-outs are when companies choose internet, phones, Wi-Fi and managed IT. FirstKnock shows you who's moving weeks ahead.",
    bestLeads: ["Office move-ins and build-outs", "Companies relocating or opening a new HQ", "Clinics and labs needing secure networks"],
    pitches: [
      { lead: "Venture firm taking a new suite", say: "Cabling, an internet circuit and Wi-Fi in place before move-in." },
      { lead: "Medical group opening a clinic", say: "A HIPAA-ready network and phones." },
    ],
  },
  pos: {
    headline: "Be the POS they pick before opening day.",
    sub: "New restaurants, cafés, shops and gyms choose a point of sale weeks before they open. Liquor licenses and store build-outs tell you exactly who and where.",
    bestLeads: ["Pending liquor licenses", "New retail and restaurant build-outs", "Gyms and studios opening"],
    pitches: [
      { lead: "New café, license pending", say: "POS with online ordering and tableside pay." },
      { lead: "Retail store build-out", say: "POS, inventory and better card rates." },
    ],
  },
  insurance: {
    headline: "Quote new businesses before their first renewal.",
    sub: "Every business that opens, moves or changes hands needs property, liability and workers' comp — usually before the doors open.",
    bestLeads: ["New restaurants and bars (liquor liability)", "Businesses moving into new spaces", "Changes of ownership"],
    pitches: [
      { lead: "Bar with a new liquor license", say: "Liquor liability, property and workers' comp in one review." },
      { lead: "Company moving offices", say: "Property, GL and cyber for the new location." },
    ],
  },
  furniture: {
    headline: "Furnish the build-out while it's still drywall.",
    sub: "Office build-outs and new leases are when companies buy workstations, conference rooms and lounge furniture. Get in during design, not after.",
    bestLeads: ["Office tenant improvements", "New leases and relocations", "Clinics and hospitality build-outs"],
    pitches: [
      { lead: "Law firm build-out", say: "Workstations and conference rooms for the new floor." },
      { lead: "Medical office opening", say: "Waiting room and exam-room furniture." },
    ],
  },
};

export const TRADE_COPY: TradeCopy[] = TRADES.map((t) => ({ id: t.id, label: t.label, emoji: t.emoji, ...COPY[t.id] }));
export const tradeCopy = (id: string) => TRADE_COPY.find((t) => t.id === id);
