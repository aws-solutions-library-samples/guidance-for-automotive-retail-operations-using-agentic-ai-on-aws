"use client";

// Customer Web · /shop — A1 Personalized Vehicle Discovery for consumers
// (design §6.1, spec §5.10). Lifestyle / budget / daily-miles intake, ranked
// rec-cards with confidence, VIN-level availability, price build + incentives,
// EV-vs-ICE comparison with TCO savings, save build, and the re-score note.
// Mobile-first, OEM brand (light) theme — set by the Shell.

import { fromZoneInput } from "@/lib/market";
import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import {
  Async, Badge, BenefitItem, Btn, Card, CompareTable, Confidence, DateTime, EmptyState, Field,
  FilterPills, IncentiveRow, InfoRow, Modal, Money, Page, PtChip, RecCard, SavingsBanner,
  SectionLabel, Slider, VehicleItem, ViewHeader, VTag, useMarketCtx, useToast,
} from "@/components/app";
import { BrandScope, useBrandName } from "@/app/garage/Brand";
import { apiPost } from "@/lib/api";
import { useMutation } from "@/lib/hooks";
import { fmtDistance, fmtMoney, fmtNumber, MarketConfig } from "@/lib/market";

type Answers = Record<string, any>;

const LIFESTYLES = [
  { id: "commute", label: "Daily commute" }, { id: "family", label: "Family hauler" },
  { id: "road_trips", label: "Road trips" }, { id: "outdoors", label: "Outdoors & gear" },
  { id: "city", label: "City driving" }, { id: "towing", label: "Towing" },
] as const;

const CHOICES: Record<string, { value: any; label: string }[]> = {
  powertrain_openness: [{ value: "ev", label: "Electric" }, { value: "open", label: "Open to anything" }, { value: "ice", label: "Gas" }],
  ownership_style: [{ value: "buy", label: "Buy" }, { value: "lease", label: "Lease" }],
  towing_or_hauling: [{ value: true, label: "Yes" }, { value: false, label: "No" }],
  road_trip_frequency: [{ value: "rarely", label: "Rarely" }, { value: "monthly", label: "Monthly" }, { value: "weekly", label: "Weekly" }],
  tech_affinity: [{ value: "low", label: "Not important" }, { value: "medium", label: "Nice to have" }, { value: "high", label: "Very important" }],
  safety_priority: [{ value: "medium", label: "Standard" }, { value: "high", label: "Top priority" }],
};
const NUMERIC = new Set(["household_size", "commute_mi_per_day"]);

const isEv = (pt?: string) => ["ev", "bev", "electric", "phev"].includes((pt || "").toLowerCase());

export default function ShopPage() {
  return (
    <BrandScope>
      <ShopView />
    </BrandScope>
  );
}

function ShopView() {
  const brandName = useBrandName();
  const cfg = useMarketCtx();
  const toast = useToast();
  const build = useMutation();

  const [lifestyle, setLifestyle] = useState<string[]>([]);
  const [budget, setBudget] = useState(45000);
  const [dailyMiles, setDailyMiles] = useState(30);
  const [answers, setAnswers] = useState<Answers>({});
  const [zip, setZip] = useState("");
  const [state, setState] = useState<{ data: any | null; error: string | null; loading: boolean }>({ data: null, error: null, loading: true });
  const [saved, setSaved] = useState<any | null>(null);
  const [tdOpen, setTdOpen] = useState(false);
  const [vinsFor, setVinsFor] = useState<any | null>(null);

  const run = async (extra: Answers = answers, nextLifestyle = lifestyle) => {
    setState((s) => ({ ...s, loading: true, error: null }));
    const a: Answers = { ...extra, lifestyle: nextLifestyle, budget_max: budget, commute_mi_per_day: dailyMiles };
    try { setState({ data: await apiPost("/shop/shortlist", { answers: a, zip: zip || undefined }), error: null, loading: false }); }
    catch (e: any) { setState({ data: null, error: e.message, loading: false }); }
  };
  useEffect(() => { run({}); /* first load: what we already know */ }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const answer = (field: string, value: any) => {
    const next = { ...answers, [field]: value };
    setAnswers(next);
    run(next);
  };
  const save = async (item: any) => {
    const out: any = await build.run("/shop/builds", {
      model_id: item.model_id, trim_id: item.recommended?.trim_id,
      packages: (item.recommended?.packages || []).map((p: any) => p.package_name),
    });
    if (out) { setSaved({ ...out, model_name: item.model_name }); toast("Build saved to My Garage", "success"); }
    else toast(build.error || "Could not save this build", "error");
  };

  const d = state.data;
  const shortlist: any[] = d?.shortlist || [];
  const ev = shortlist.find((i) => isEv(i.powertrain));
  const ice = shortlist.find((i) => !isEv(i.powertrain));

  return (
    <Page>
      <ViewHeader
        title="Find your match"
        subtitle={`A few quick questions and ${brandName} shortlists the vehicles that fit your life.`}
        actions={<Link href="/garage" className="btn btn-secondary btn-sm">My Garage</Link>}
      />

      {/* ---- intake: lifestyle · budget · daily miles -------------------- */}
      <Card title="Tell us about your life">
        <div className="flex flex-col gap-4">
          <div>
            <SectionLabel>How you drive</SectionLabel>
            <FilterPills options={LIFESTYLES as any} value={lifestyle} multi
                         onChange={(v: string[]) => { setLifestyle(v); run(answers, v); }} />
          </div>
          <div className="grid gap-4 md:grid-cols-2">
            <Slider label="Budget (out the door)" value={budget} min={15000} max={120000} step={2500}
                    onChange={setBudget} format={(v) => money(cfg, v)} />
            <Slider label="Daily driving" value={dailyMiles} min={5} max={150} step={5}
                    onChange={setDailyMiles} format={(v) => distance(cfg, v)} />
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <Field label="Postal code" hint="For VIN availability and local incentives.">
              {(id) => <input id={id} className="input" style={{ width: 130 }} inputMode="numeric" value={zip}
                              onChange={(e) => setZip(e.target.value)} />}
            </Field>
            <Btn kind="primary" size="lg" onClick={() => run()} disabled={state.loading}>
              {state.loading ? "Matching…" : "Update my matches"}
            </Btn>
          </div>
          {d?.questions?.length > 0 && (
            <div className="flex flex-col gap-3 pt-2" style={{ borderTop: "1px solid var(--border)" }}>
              <SectionLabel>Help us narrow it down ({d.questions.length} quick question{d.questions.length === 1 ? "" : "s"})</SectionLabel>
              {d.questions.map((q: any) => (
                <div key={q.field}>
                  <div className="text-sm font-semibold mb-2">{q.question}</div>
                  {NUMERIC.has(q.field) ? (
                    <NumberAnswer onSubmit={(v) => answer(q.field, v)} />
                  ) : (
                    <div className="flex flex-wrap gap-2" role="group" aria-label={q.question}>
                      {(CHOICES[q.field] || [{ value: "yes", label: "Yes" }, { value: "no", label: "No" }]).map((c) => (
                        <button key={String(c.value)} className="filter-pill min-h-[44px]" onClick={() => answer(q.field, c.value)}>{c.label}</button>
                      ))}
                    </div>
                  )}
                </div>
              ))}
            </div>
          )}
        </div>
      </Card>

      {/* ---- ranked recommendations -------------------------------------- */}
      <Card title="Your matches" actions={d?.rescored_at
        ? <Badge tone="blue" title="Matches were re-scored">Re-scored <DateTime value={d.rescored_at} /></Badge>
        : undefined}>
        <Async q={{ data: d, error: state.error, loading: state.loading, reload: () => run() }} rows={4}>
          {(data) => (data.shortlist || []).length === 0
            ? <EmptyState>Nothing fits yet — try widening your budget or changing your answers.</EmptyState>
            : (
              <div className="flex flex-col gap-4" aria-live="polite">
                {data.shortlist.map((item: any) => (
                  <ShortlistCard key={item.model_id} item={item} pending={build.pending}
                                 onSave={() => save(item)} onTestDrive={() => setTdOpen(true)}
                                 onVins={() => setVinsFor(item)} />
                ))}
              </div>
            )}
        </Async>
        <p className="text-[.72rem] dim mt-3 mb-0">
          When inventory or incentives change, your matches are re-scored automatically and your
          saved builds pick up the new pricing in My Garage.
        </p>
      </Card>

      {/* ---- EV vs ICE + TCO ---------------------------------------------- */}
      {ev && ice && <EvVsIce ev={ev} ice={ice} />}

      {saved && (
        <Card accent="success" className="m-0">
          <span role="status">
            <b>{saved.model_name} saved.</b> Estimated <Money value={saved.price_total} /> · <Money value={saved.est_monthly_buy} /> per month.{" "}
            <Link href="/garage" className="underline">View in My Garage</Link>
          </span>
        </Card>
      )}

      <TestDriveModal open={tdOpen} onClose={() => setTdOpen(false)} />
      <VinModal item={vinsFor} onClose={() => setVinsFor(null)} />
    </Page>
  );
}

// ---- one recommendation ------------------------------------------------------

function ShortlistCard({ item, pending, onSave, onTestDrive, onVins }: {
  item: any; pending: boolean; onSave: () => void; onTestDrive: () => void; onVins: () => void }) {
  const conf = item.confidence ?? item.match_score ?? item.score;
  const confPct = conf != null ? Math.round((Number(conf) > 1 ? Number(conf) : Number(conf) * 100)) : null;
  const incentives: any[] = item.price?.incentives || [];
  const best = item.availability?.best;
  return (
    <RecCard
      title={<span className="flex items-center gap-2 flex-wrap">{item.model_name} <PtChip powertrain={item.powertrain} /></span>}
      right={<span className="flex items-center gap-2">
        <Confidence>{confPct != null ? `${confPct}% match` : item.rank === 1 ? "Top match" : "Good match"}</Confidence>
        {item.recommended?.within_budget === false ? <Badge tone="orange">Over budget</Badge> : <Badge tone="green">Fits budget</Badge>}
      </span>}
    >
      <VehicleItem rank={item.rank}
                   name={`${item.recommended?.trim_name || ""}${item.body ? ` · ${item.body}` : ""}`}
                   specs={<>{item.seats ? `${item.seats} seats` : null}{item.features?.length ? `${item.seats ? " · " : ""}${item.features.slice(0, 3).join(" · ")}` : null}</>}
                   tags={(item.recommended?.packages || []).map((p: any) => <VTag key={p.package_name} tone="blue">{p.package_name}</VTag>)} />
      <div className="grid gap-3 md:grid-cols-2 mt-2">
        <div>
          <SectionLabel>Why this fits you</SectionLabel>
          <ul className="compete-list mt-1">
            {(item.why || []).map((w: any, i: number) => <li key={i} title={w.field ? `based on: ${w.field}` : undefined}>{w.text || w}</li>)}
          </ul>
          {(item.recommended?.packages || []).map((p: any) => (
            <div key={p.package_name} className="text-xs muted mt-1">{p.package_name}: {p.reason}</div>
          ))}
        </div>
        <div>
          <SectionLabel>Your price build</SectionLabel>
          <InfoRow label="Vehicle" value={<Money value={item.price?.vehicle ?? item.price?.msrp ?? null} />} />
          {incentives.length > 0
            ? incentives.slice(0, 4).map((inc: any, i: number) => (
                <IncentiveRow key={i} eligible={inc.eligible !== false} name={inc.name}
                              right={inc.kind === "apr" || inc.amount == null
                                ? <span className="dim text-xs">rate program · ask the dealer</span>
                                : <span className="font-semibold">{inc.reduces_price === false ? "" : "−"}<Money value={inc.amount} /></span>} />
              ))
            : item.price?.incentives_total > 0 && (
                <IncentiveRow eligible name="Incentives you qualify for"
                              right={<span className="font-semibold">−<Money value={item.price.incentives_total} /></span>} />
              )}
          <InfoRow label="Est. total out the door" value={<b><Money value={item.price?.total_out_the_door} /></b>} />
          <InfoRow label="Est. monthly (buy)" value={<Money value={item.price?.est_monthly?.buy} />} />
          <InfoRow label="Est. monthly (lease)" value={<Money value={item.price?.est_monthly?.lease} />} />
          <InfoRow label="Availability" value={best
            ? <>{best.match === "in_transit" ? `Arrives in ${best.eta_days} days` : "In stock"} · {best.dealer_name}</>
            : "Build to order"} />
        </div>
      </div>
      <div className="flex flex-wrap gap-2 mt-3">
        <Btn kind="present" size="lg" disabled={pending} onClick={onSave}>Save this build</Btn>
        <Btn kind="alt" size="lg" onClick={onVins}>See VINs near me</Btn>
        <Btn kind="alt" size="lg" onClick={onTestDrive}>Book a test drive</Btn>
      </div>
      {item.price?.est_monthly?.buy_term_months && (
        <div className="text-[.68rem] dim mt-2">
          Estimates: {item.price.est_monthly.buy_term_months} mo at {item.price.est_monthly.apr_pct}% APR, before credit review.
        </div>
      )}
    </RecCard>
  );
}

// ---- VIN-level availability ---------------------------------------------------

function VinModal({ item, onClose }: { item: any | null; onClose: () => void }) {
  const options: any[] = (item?.availability?.options || (item?.availability?.best ? [item.availability.best] : [])).filter(Boolean);
  return (
    <Modal open={!!item} onClose={onClose} title={`Available near you${item ? ` — ${item.model_name}` : ""}`}>
      {options.length === 0
        ? <EmptyState>No matching vehicles nearby right now — a dealer can order this build for you.</EmptyState>
        : (
          <div className="flex flex-col gap-2">
            {options.map((v: any, i: number) => (
              <div key={v.vin || i} className="info-row">
                <span>
                  <b>{v.trim_name || item?.recommended?.trim_name}</b>{v.color ? ` · ${v.color}` : ""}
                  <div className="dim text-xs">
                    {v.vin && <span className="mono">{v.vin} · </span>}
                    {v.dealer_name} · {v.match === "in_transit" ? `arrives in ${v.eta_days} days` : "in stock"}
                  </div>
                </span>
                <span className="info-value"><Money value={v.list_price} /></span>
              </div>
            ))}
          </div>
        )}
    </Modal>
  );
}

// ---- EV vs ICE comparison -------------------------------------------------------

function EvVsIce({ ev, ice }: { ev: any; ice: any }) {
  const rows = useMemo(() => {
    const out: { label: string; a: React.ReactNode; b: React.ReactNode; winner?: "a" | "b" }[] = [];
    const push = (label: string, a?: number | null, b?: number | null, lowerWins = true) => {
      if (a == null && b == null) return;
      out.push({
        label, a: <Money value={a ?? null} />, b: <Money value={b ?? null} />,
        winner: a != null && b != null ? ((lowerWins ? a < b : a > b) ? "a" : "b") : undefined,
      });
    };
    push("Price after incentives", ev.price?.total_out_the_door, ice.price?.total_out_the_door);
    push("Est. monthly (buy)", ev.price?.est_monthly?.buy, ice.price?.est_monthly?.buy);
    push("Incentives", ev.price?.incentives_total, ice.price?.incentives_total, false);
    push("5-year energy vs fuel", ev.tco?.energy_5yr, ice.tco?.fuel_5yr ?? ice.tco?.energy_5yr);
    push("5-year maintenance", ev.tco?.maintenance_5yr, ice.tco?.maintenance_5yr);
    push("5-year total cost", ev.tco?.total_5yr, ice.tco?.total_5yr);
    return out;
  }, [ev, ice]);
  const savings = ev.tco?.total_5yr != null && ice.tco?.total_5yr != null ? ice.tco.total_5yr - ev.tco.total_5yr : null;
  return (
    <Card title="Electric vs gas — side by side">
      <div className="flex flex-col gap-3">
        {savings != null && savings > 0 && (
          <SavingsBanner label="Estimated 5-year savings going electric" value={<Money value={savings} />} />
        )}
        <CompareTable aLabel={<>{ev.model_name} <PtChip powertrain={ev.powertrain} /></>}
                      bLabel={<>{ice.model_name} <PtChip powertrain={ice.powertrain} /></>}
                      rows={rows}
                      footnote="Estimates use your daily driving and local energy and fuel prices; your dealer can confirm exact figures." />
        <div className="grid gap-2 sm:grid-cols-3">
          <BenefitItem icon="⚡" title="Charge at home">Start every day full — most daily driving never needs a public charger.</BenefitItem>
          <BenefitItem icon="🔧" title="Less maintenance">No oil changes and fewer moving parts to service.</BenefitItem>
          <BenefitItem icon="🏷" title="EV incentives">Federal, state and brand incentives are already in the prices above.</BenefitItem>
        </div>
      </div>
    </Card>
  );
}

// ---- small inputs ----------------------------------------------------------------

function NumberAnswer({ onSubmit }: { onSubmit: (v: number) => void }) {
  const [v, setV] = useState("");
  return (
    <form className="flex gap-2" onSubmit={(e) => { e.preventDefault(); if (v) onSubmit(Number(v)); }}>
      <input className="input" style={{ maxWidth: 120 }} inputMode="numeric" value={v}
             onChange={(e) => setV(e.target.value.replace(/[^0-9]/g, ""))} aria-label="Answer" />
      <Btn kind="primary" type="submit" disabled={!v}>OK</Btn>
    </form>
  );
}

function TestDriveModal({ open, onClose }: { open: boolean; onClose: () => void }) {
  const [when, setWhen] = useState("");
  const m = useMutation();
  const toast = useToast();
  const [done, setDone] = useState<any | null>(null);
  const book = async () => {
    const out = await m.run("/shop/test-drive", when ? { slot_start: fromZoneInput(when) } : {});
    if (out) { setDone(out); toast("Test drive booked", "success"); } else toast(m.error || "Could not book", "error");
  };
  return (
    <Modal open={open} onClose={() => { setDone(null); onClose(); }} title="Book a test drive"
           footer={!done && <Btn kind="primary" size="lg" onClick={book} disabled={m.pending}>
             {m.pending ? "Booking…" : when ? "Book this time" : "Book next available"}</Btn>}>
      {done ? (
        <p className="m-0" role="status">You&apos;re booked for <b><DateTime value={done.slot_start} /></b>
          {done.dealer_name ? ` at ${done.dealer_name}` : ""}. We&apos;ll send a reminder.</p>
      ) : (
        <Field label="Preferred time (optional)" hint="Leave empty and we'll pick the next open slot.">
          {(id) => <input id={id} className="input" type="datetime-local" value={when} onChange={(e) => setWhen(e.target.value)} />}
        </Field>
      )}
    </Modal>
  );
}

// market-aware strings for slider labels (components can't render inside format())
const money = (cfg: MarketConfig | null, v: number) =>
  cfg ? fmtMoney(cfg, v) : fmtNumber({ locale: "en-US" } as MarketConfig, v);
const distance = (cfg: MarketConfig | null, v: number) =>
  cfg ? `${fmtDistance(cfg, v)} per day` : `${fmtNumber({ locale: "en-US" } as MarketConfig, v)} per day`;
