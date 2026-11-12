"use client";

// My Garage sections (design §6.1, spec §5.12) — one card per journey concern,
// from the customer's side. Each section owns its read + actions and renders
// loading / error / empty states; every figure comes from the API and is
// formatted through the market formatters. Mobile-first.

import { fromZoneInput } from "@/lib/market";
import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import {
  Async, Badge, Btn, Card, DateTime, Distance, EmptyState, ErrorState, Field, InfoRow, JourneyActivityFeed,
  LoadingBlock, Modal, Money, SectionLabel, SourceRef, Stepper, stageLabel, useToast,
} from "@/components/app";
import { apiGet, apiPost } from "@/lib/api";
import { useApi, useMutation } from "@/lib/hooks";

// ---- Journey tracker ---------------------------------------------------------

export function JourneySection() {
  const q = useApi<any>("/garage/journey", { pollMs: 60000 });
  return (
    <Card title="Your journey">
      <Async q={q}>
        {(d) => !d.journey ? (
          <EmptyState>No purchase in progress. <Link className="underline" href="/shop">Start shopping</Link>.</EmptyState>
        ) : (
          <div className="flex flex-col gap-3">
            <div className="overflow-x-auto"><Stepper current={d.journey.stage} compact /></div>
            <div className="text-sm">
              <b>Done: {stageLabel(d.journey.stage)}.</b>{" "}
              <span className="muted">{d.journey.next || "We'll let you know the next step."}</span>
            </div>
            <div className="flex flex-wrap items-center gap-3 text-sm">
              {d.journey.advisor && <span>Your advisor: <b>{d.journey.advisor.name}</b></span>}
              {d.journey.dealer?.name && <span className="muted">{d.journey.dealer.name}</span>}
              <Link href="/shop/chat" className="btn btn-primary btn-sm">Message my advisor</Link>
            </div>
            {d.journey.journey_id && (
              <div>
                <SectionLabel>Recent activity</SectionLabel>
                <JourneyActivityFeed journeyId={d.journey.journey_id} audience="customer" limit={6} />
              </div>
            )}
          </div>
        )}
      </Async>
    </Card>
  );
}

// ---- Saved builds --------------------------------------------------------------

export function BuildsSection() {
  const q = useApi<any>("/garage/builds");
  const [avail, setAvail] = useState<{ id: string; data: any | null; error?: string } | null>(null);
  const toast = useToast();
  const findVin = async (id: string) => {
    setAvail({ id, data: null });
    try { setAvail({ id, data: await apiGet(`/garage/builds/${encodeURIComponent(id)}/availability`) }); }
    catch (e: any) { setAvail({ id, data: null, error: e.message }); }
  };
  const share = async (b: any) => {
    const url = `${window.location.origin}/shop?build=${b.share_token}`;
    try { await navigator.clipboard.writeText(url); toast("Share link copied", "success"); }
    catch { toast(url); }
  };
  return (
    <Card title="Saved builds">
      <Async q={q}>
        {(d) => d.builds.length === 0
          ? <EmptyState>No saved builds yet. <Link className="underline" href="/shop">Find your match</Link>.</EmptyState>
          : (
            <div className="flex flex-col gap-3">
              {d.builds.map((b: any) => (
                <div key={b.config_id} className="rec-card p-4 flex flex-col gap-2">
                  <div className="flex justify-between gap-2 flex-wrap"><b>{b.model_name}</b><Badge tone="blue">{b.trim_name}</Badge></div>
                  {b.color && <div className="muted text-sm">{b.color}</div>}
                  <InfoRow label="Vehicle" value={<Money value={b.price_vehicle} />} />
                  <InfoRow label="Off the price" value={b.incentives_total > 0 ? <>−<Money value={b.incentives_total} /></> : <span className="muted">no point-of-sale incentive</span>} />
                  <InfoRow label="Estimated total" value={<b><Money value={b.price_total} /></b>} />
                  {/* the payment of record (the deal's, once booked) is THE number; the build's own figure is an estimate */}
                  {b.payment?.basis === "deal" ? (
                    <InfoRow label="Your monthly payment" value={<b><Money value={b.payment.monthly} /> <span className="muted text-xs font-normal">· {b.payment.apr_pct}% for {b.payment.term_months} mo · {b.payment.lender}</span></b>} />
                  ) : b.payment?.basis === "offer" ? (
                    <InfoRow label="Monthly (offered)" value={<><Money value={b.payment.monthly} /> <span className="muted text-xs">· {b.payment.apr_pct}% for {b.payment.term_months} mo</span></>} />
                  ) : (
                    <InfoRow label="Est. monthly (buy)" value={<><Money value={b.est_monthly_buy} /> <span className="muted text-xs">· estimate before financing</span></>} />
                  )}
                  {b.incentives?.length > 0 && (
                    <div className="text-[.72rem] muted flex flex-col gap-0.5">
                      {b.incentives.map((i: any) => (
                        <div key={i.incentive_id}>{i.name}{i.kind === "price" && i.amount ? <> · <Money value={i.amount} /> off</> : i.kind === "apr" ? " · rate program" : i.amount ? <> · <Money value={i.amount} /> claimed separately</> : ""}</div>
                      ))}
                    </div>
                  )}
                  <div className="flex gap-2 flex-wrap mt-1">
                    <Btn kind="primary" size="sm" onClick={() => findVin(b.config_id)}>Find this VIN near me</Btn>
                    <Btn kind="alt" size="sm" onClick={() => share(b)}>Share</Btn>
                  </div>
                  <SourceRef refId={b.source_ref} />
                </div>
              ))}
            </div>
          )}
      </Async>
      <Modal open={!!avail} onClose={() => setAvail(null)} title="Available near you">
        {avail?.error ? <ErrorState error={avail.error} />
          : !avail?.data ? <LoadingBlock rows={2} />
          : (avail.data.options || []).filter(Boolean).length === 0
            ? <EmptyState>No matching vehicles right now — your advisor can order one.</EmptyState>
            : (
              <div className="flex flex-col gap-2">
                {avail.data.options.filter(Boolean).map((v: any) => (
                  <div key={v.vin} className="info-row">
                    <span>
                      <b>{v.trim_name}</b> {v.color ? `· ${v.color}` : ""}
                      <div className="dim text-xs">{v.vin && <span className="mono">{v.vin} · </span>}{v.dealer_name} · {v.match === "in_transit" ? `arrives in ${v.eta_days} days` : "in stock"}</div>
                    </span>
                    <span className="info-value"><Money value={v.list_price} /></span>
                  </div>
                ))}
              </div>
            )}
      </Modal>
    </Card>
  );
}

// ---- Appointments ----------------------------------------------------------------

export function AppointmentsSection() {
  const q = useApi<any>("/garage/appointments");
  const m = useMutation("PATCH");
  const book = useMutation();
  const toast = useToast();
  const [resched, setResched] = useState<any | null>(null);
  const [when, setWhen] = useState("");
  const [bookOpen, setBookOpen] = useState(false);
  const [bookWhen, setBookWhen] = useState("");
  const act = async (a: any, body: any, msg: string) => {
    const out = await m.run(`/garage/appointments/${encodeURIComponent(a.appointment_id)}`, body);
    if (out) { toast(msg, "success"); setResched(null); q.reload(); } else toast(m.error || "Could not update", "error");
  };
  // Book a test drive straight from My Garage (spec §2.6.1 — /shop, My Garage, Lead Desk)
  const bookTestDrive = async () => {
    const out: any = await book.run("/shop/test-drive", bookWhen ? { slot_start: fromZoneInput(bookWhen) } : {});
    if (out) {
      toast(`Test drive booked${out.dealer_name ? ` at ${out.dealer_name}` : ""}`, "success");
      setBookOpen(false); setBookWhen(""); q.reload();
    } else toast(book.error || "Could not book a test drive", "error");
  };
  const label = (t: string) => ({ test_drive: "Test drive", delivery: "Delivery", service: "Service", service_pickup: "Service pickup" } as Record<string, string>)[t] || t;
  return (
    <Card title="Appointments" actions={<Btn kind="alt" size="sm" onClick={() => setBookOpen(true)}>Book a test drive</Btn>}>
      <Async q={q}>
        {(d) => (
          <div className="flex flex-col gap-2">
            {d.upcoming.length === 0 && <EmptyState>No upcoming appointments.</EmptyState>}
            {d.upcoming.map((a: any) => (
              <div key={a.appointment_id} className="rec-card p-3 flex flex-wrap items-center justify-between gap-2">
                <div><b>{label(a.type)}</b> · <DateTime value={a.slot_start} /><div className="dim text-xs">{a.dealer_name}</div></div>
                <div className="flex gap-2">
                  <Btn kind="alt" size="sm" onClick={() => { setResched(a); setWhen(""); }}>Reschedule</Btn>
                  <Btn kind="ghost" size="sm" disabled={m.pending}
                       onClick={() => window.confirm("Cancel this appointment?") && act(a, { action: "cancel" }, "Appointment cancelled")}>Cancel</Btn>
                </div>
              </div>
            ))}
            {d.past.length > 0 && (
              <details className="text-sm">
                <summary className="cursor-pointer muted">Past appointments ({d.past.length})</summary>
                {d.past.map((a: any) => (
                  <InfoRow key={a.appointment_id} label={<>{label(a.type)} · <DateTime value={a.slot_start} mode="date" /></>} value={a.status} />
                ))}
              </details>
            )}
          </div>
        )}
      </Async>
      <Modal open={!!resched} onClose={() => setResched(null)} title="Reschedule"
             footer={<Btn kind="primary" size="lg" disabled={!when || m.pending}
                          onClick={() => act(resched, { action: "reschedule", slot_start: fromZoneInput(when) }, "Appointment moved")}>Confirm</Btn>}>
        <Field label="New date and time">{(id) => <input id={id} className="input" type="datetime-local" value={when} onChange={(e) => setWhen(e.target.value)} />}</Field>
      </Modal>
      <Modal open={bookOpen} onClose={() => setBookOpen(false)} title="Book a test drive"
             footer={<Btn kind="primary" size="lg" onClick={bookTestDrive} disabled={book.pending}>
               {book.pending ? "Booking…" : bookWhen ? "Book this time" : "Book next available"}</Btn>}>
        <Field label="Preferred time (optional)" hint="Leave empty and we'll pick the next open slot at your dealer.">
          {(id) => <input id={id} className="input" type="datetime-local" value={bookWhen} onChange={(e) => setBookWhen(e.target.value)} />}
        </Field>
      </Modal>
    </Card>
  );
}

// ---- Financing --------------------------------------------------------------------

export function FinanceSection() {
  const q = useApi<any>("/garage/finance");
  const m = useMutation();
  const toast = useToast();
  const [result, setResult] = useState<any | null>(null);
  const start = async () => {
    const out = await m.run("/garage/finance/prequal", {});
    if (out) { setResult(out); toast("Payment options ready", "success"); q.reload(); } else toast(m.error || "Could not start", "error");
  };
  const statusText: Record<string, string> = {
    not_started: "Not started", prequal_in_progress: "In review", offers_presented: "Options presented", deal_agreed: "Deal agreed",
  };
  return (
    <Card title="Financing" actions={<Badge tone="blue">{statusText[q.data?.status] || q.data?.status || "—"}</Badge>}>
      <Async q={q}>
        {(d) => (
          <div className="flex flex-col gap-3">
            {d.payment && (
              <InfoRow label={d.payment.basis === "deal" ? "Your monthly payment" : "Offered payment"}
                       value={<b><Money value={d.payment.monthly} /> <span className="muted text-xs font-normal">· {d.payment.apr_pct}% for {d.payment.term_months} mo{d.payment.lender ? ` · ${d.payment.lender}` : ""}</span></b>} />
            )}
            {(result?.scenarios || d.offers).length === 0 ? (
              <div className="flex flex-wrap items-center gap-3">
                <span className="muted text-sm">See your payment options with a soft check — it doesn&apos;t affect your credit.</span>
                <Btn kind="primary" size="lg" onClick={start} disabled={m.pending}>{m.pending ? "Checking…" : "Start pre-qualification"}</Btn>
              </div>
            ) : (
              <div className="overflow-x-auto">
                <table className="data-table">
                  <caption className="muted text-xs text-left pb-1">{d.payment?.basis === "deal" ? "Scenarios presented before the deal was agreed" : "Payment scenarios (soft check, illustrative)"}</caption>
                  <thead><tr><th>Term</th><th className="num">APR</th><th className="num">Monthly</th><th>Lender</th></tr></thead>
                  <tbody>
                    {(result?.scenarios || d.offers).slice(0, 6).map((o: any, i: number) => (
                      <tr key={o.offer_id || i}>
                        <td>{o.term_months} mo</td><td className="num">{o.apr_pct}%</td>
                        <td className="num"><b><Money value={o.monthly_payment ?? o.monthly} /></b></td>
                        <td>{o.lender || o.tier || "—"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
            {d.deal && (
              <InfoRow label="Your deal"
                       value={<span className="text-sm">{String(d.deal.type || "purchase").replace(/_/g, " ")} · {String(d.deal.status || "in progress").replace(/_/g, " ")}{d.deal.price_total != null && <> · <Money value={d.deal.price_total} /></>}</span>} />
            )}
            {/* F&I protection menu, customer-facing mode (spec §2.6.1, §5.13):
                products available in your market; the personalized menu your
                dealer presents arrives under Documents. */}
            {(d.finance_products || []).length > 0 && (
              <div className="pt-2" style={{ borderTop: "1px solid var(--border)" }}>
                <SectionLabel>Protection &amp; products you can add</SectionLabel>
                <div className="flex flex-col">
                  {d.finance_products.map((p: any) => (
                    <InfoRow key={p.code || p.name} label={p.name}
                             value={<span className="dim text-xs">{p.terms_months?.length ? `${p.terms_months.join(" / ")} month terms` : "ask your advisor"}</span>} />
                  ))}
                </div>
                <p className="text-[.72rem] dim m-0 mt-1">
                  When your dealer presents a personalized protection menu, it appears here and under
                  Documents as &quot;F&amp;I protection menu&quot; — nothing is added without your OK.
                </p>
              </div>
            )}
            {(result?.disclaimer || d.note) && <p className="text-[.72rem] dim m-0">{result?.disclaimer || d.note}</p>}
            {d.disclosures?.length > 0 && <div className="text-[.72rem] dim">{d.disclosures.join(" · ")}</div>}
          </div>
        )}
      </Async>
    </Card>
  );
}

// ---- Trade-in --------------------------------------------------------------------

export function TradeSection() {
  const q = useApi<any>("/garage/trade");
  const m = useMutation();
  const toast = useToast();
  const [open, setOpen] = useState(false);
  const [mileage, setMileage] = useState("");
  const [condition, setCondition] = useState("");
  const [photos, setPhotos] = useState<string[]>([]);
  const refresh = async () => {
    const body: any = {};
    if (mileage) body.mileage = Number(mileage);
    if (condition) body.condition = condition;
    if (photos.length) body.photos = photos;
    const out = await m.run("/garage/trade/refresh", body);
    if (out) { toast("Trade-in value refreshed", "success"); setOpen(false); setPhotos([]); q.reload(); }
    else toast(m.error || "Could not refresh", "error");
  };
  return (
    <Card title="Trade-in" actions={<Btn kind="alt" size="sm" onClick={() => setOpen(true)} disabled={!q.data?.current}>Update details</Btn>}>
      <Async q={q}>
        {(d) => !d.current ? <EmptyState>No trade-in on file. Your advisor can value your current vehicle.</EmptyState> : (
          <div className="flex flex-col gap-1">
            <div className="font-semibold">{d.current.year} {d.current.make} {d.current.model} · <Distance value={d.current.mileage} /></div>
            <InfoRow label="Estimated value" value={<><Money value={d.current.range?.low} /> – <Money value={d.current.range?.high} /></>} />
            <InfoRow label="Payoff" value={<Money value={d.current.payoff} />} />
            <InfoRow label="Your equity"
                     value={<b style={{ color: d.current.equity >= 0 ? "var(--success)" : "var(--danger)" }}>
                       <Money value={d.current.equity} />{d.current.equity >= 0 ? " ahead" : " owed"}</b>} />
            <InfoRow label={d.current.expired ? "Expired" : "Valid until"} value={<DateTime value={d.current.expires_at} mode="date" />} />
            {d.current.expired && (
              <div className="text-[.75rem]" style={{ color: "var(--warning)" }}>
                This value has expired — update your mileage or add photos to refresh it.
              </div>
            )}
            <SourceRef refId={d.current.source_ref} />
          </div>
        )}
      </Async>
      <Modal open={open} onClose={() => setOpen(false)} title="Update trade-in details"
             footer={<Btn kind="primary" size="lg" onClick={refresh} disabled={m.pending || (!mileage && !condition && !photos.length)}>
               {m.pending ? "Refreshing…" : "Refresh value"}</Btn>}>
        <Field label="Current mileage">
          {(id) => <input id={id} className="input" inputMode="numeric" value={mileage}
                          onChange={(e) => setMileage(e.target.value.replace(/[^0-9]/g, ""))} />}
        </Field>
        <Field label="Condition">
          {(id) => (
            <select id={id} className="select" value={condition} onChange={(e) => setCondition(e.target.value)}>
              <option value="">Unchanged</option><option value="excellent">Excellent</option><option value="good">Good</option>
              <option value="fair">Fair</option><option value="poor">Poor</option>
            </select>
          )}
        </Field>
        <Field label="Photos of your vehicle" hint="Clear photos of the exterior, interior and odometer sharpen your valuation.">
          {(id) => <input id={id} className="input" type="file" accept="image/*" multiple
                          onChange={(e) => setPhotos(Array.from(e.target.files || []).map((f) => f.name))} />}
        </Field>
        {photos.length > 0 && <div className="text-xs muted">{photos.length} photo{photos.length === 1 ? "" : "s"} attached: {photos.join(", ")}</div>}
      </Modal>
    </Card>
  );
}

// ---- Documents ---------------------------------------------------------------------

export function DocumentsSection() {
  const q = useApi<any>("/garage/documents");
  // e-sign status is always rendered as text, never color alone (WCAG, design §6.1)
  const esignText = (s?: string | null) =>
    ({ signed: "Signed", pending: "Awaiting your signature", sent: "Sent for signature", declined: "Signature declined", not_required: "No signature needed" } as Record<string, string>)[s || ""] || null;
  return (
    <Card title="Documents">
      <Async q={q}>
        {(d) => (
          <div className="flex flex-col gap-3">
            {d.documents.length === 0
              ? <EmptyState>Documents your dealer shares — your deal jacket, invoices and reports — will appear here.</EmptyState>
              : (
                <div className="flex flex-col">
                  {d.documents.map((doc: any) => (
                    <InfoRow key={doc.document_id || doc.kind} label={<>📄 {doc.label}</>}
                             value={<span className="text-xs">
                               {esignText(doc.esign_status) && <b>{esignText(doc.esign_status)} · </b>}
                               <span className="dim"><DateTime value={doc.created_at} mode="date" /> · ask your advisor for a copy</span>
                             </span>} />
                  ))}
                </div>
              )}
            {(d.delivery_checklist || []).length > 0 && (
              <div>
                <SectionLabel>Delivery checklist</SectionLabel>
                <ul className="list-none m-0 p-0 flex flex-col gap-1">
                  {d.delivery_checklist.map((c: any, i: number) => (
                    <li key={i} className="text-sm flex items-center gap-2">
                      <span aria-hidden>{c.done ? "✅" : "○"}</span>
                      <span className={c.done ? "muted" : undefined}>{c.item}{c.done ? " — done" : ""}</span>
                    </li>
                  ))}
                </ul>
              </div>
            )}
          </div>
        )}
      </Async>
    </Card>
  );
}

// ---- Delivery-day survey (spec §2.6.1: delivery-day updates & survey) ------------------
//
// Shown for 30 days after a delivery; the response is written through
// POST /garage/survey (rating + comment + VIN land on the customer record and
// the journey timeline), falling back to the chat endpoint where that route
// isn't deployed. Dismissal is remembered per VIN on this device.

const RATING_LABELS = ["Poor", "Fair", "Good", "Very good", "Excellent"];

export function DeliverySurveyCard() {
  const q = useApi<any>("/garage/ownership");
  const m = useMutation();
  const toast = useToast();
  const [rating, setRating] = useState<number | null>(null);
  const [comment, setComment] = useState("");
  const [done, setDone] = useState(false);
  const [hidden, setHidden] = useState(true);

  // the server says which recently delivered car still awaits this customer's survey; the browser
  // remembers nothing across sessions (a per-VIN localStorage flag hid the card for the next demo
  // buyer who happened to get the same car — gap r7.1). "Maybe later" hides it for this tab only.
  const recent = useMemo(() => (q.data?.vehicles || []).find((v: any) => v.delivery_survey === "pending") || null, [q.data]);
  const key = recent ? `garage-delivery-survey-${recent.vin}` : null;
  useEffect(() => {
    if (!key) { setHidden(true); return; }
    try { setHidden(window.sessionStorage.getItem(key) === "dismissed"); } catch { setHidden(false); }
  }, [key]);

  if (!recent || hidden) return null;

  const dismiss = () => { setHidden(true); try { if (key) window.sessionStorage.setItem(key, "dismissed"); } catch { /* private mode */ } };
  const submit = async () => {
    if (rating == null) return;
    const vehicle = [recent.year, recent.make, recent.model].filter(Boolean).join(" ");
    const text = `Delivery survey for my ${vehicle}: ${rating} out of 5 (${RATING_LABELS[rating - 1]}).${comment.trim() ? ` ${comment.trim()}` : ""}`;
    // Structured survey write first; where that route isn't deployed the answer
    // still reaches A2 through the chat thread (the original path).
    let out: any = null;
    try {
      out = await apiPost<any>("/garage/survey", { rating, comment: comment.trim() || undefined, vin: recent.vin });
    } catch {
      out = await m.run("/shop/chat", { text });
    }
    if (out) {
      setDone(true);
      q.reload();                       // the record now says "submitted"; nothing to remember locally
      toast("Thanks for your feedback", "success");
    } else toast(m.error || "Could not send your feedback — please try again", "error");
  };

  return (
    <Card title="How was your delivery?"
          actions={!done && <Btn kind="ghost" size="sm" onClick={dismiss}>Maybe later</Btn>}>
      {done ? (
        <p className="m-0 text-sm" role="status">
          <b>Thank you.</b> Your feedback went to your dealer — your advisor will follow up if anything
          wasn&apos;t right.
        </p>
      ) : (
        <div className="flex flex-col gap-3">
          <p className="m-0 text-sm muted">
            You took delivery of your {[recent.year, recent.make, recent.model].filter(Boolean).join(" ")}{" "}
            on <DateTime value={recent.delivered_at} mode="date" />. Tell us how it went — it takes a minute.
          </p>
          <div className="flex flex-wrap gap-2" role="radiogroup" aria-label="Rate your delivery from 1 to 5">
            {RATING_LABELS.map((label, i) => (
              <button key={label} className={`filter-pill min-h-[44px] ${rating === i + 1 ? "active" : ""}`}
                      role="radio" aria-checked={rating === i + 1}
                      onClick={() => setRating(i + 1)}>
                {i + 1} · {label}
              </button>
            ))}
          </div>
          <Field label="Anything we should know? (optional)">
            {(id) => <textarea id={id} className="textarea min-h-[72px]" value={comment}
                               onChange={(e) => setComment(e.target.value)} />}
          </Field>
          <div>
            <Btn kind="primary" size="lg" onClick={submit} disabled={rating == null || m.pending}>
              {m.pending ? "Sending…" : "Send feedback"}
            </Btn>
          </div>
        </div>
      )}
    </Card>
  );
}

// ---- Ownership (after DELIVER) --------------------------------------------------------

export function OwnershipSection() {
  const q = useApi<any>("/garage/ownership");
  const isEv = (pt?: string) => ["ev", "bev", "electric", "phev"].includes((pt || "").toLowerCase());
  return (
    <Card title="Ownership">
      <Async q={q}>
        {(d) => (
          <div className="flex flex-col gap-3">
            {d.upgrade_offer && (
              <div className="ai-insight m-0" role="note">
                <div className="ai-insight-title">Upgrade opportunity</div>
                <p className="m-0 text-sm">You have <b><Money value={d.upgrade_offer.equity} /></b> in equity. {d.upgrade_offer.message}{" "}
                  <Link href="/shop" className="underline">See what it gets you</Link>.</p>
              </div>
            )}
            {d.vehicles.length === 0
              ? <EmptyState>Vehicles you own will appear here after delivery.</EmptyState>
              : d.vehicles.map((v: any) => (
                <div key={v.vin} className="rec-card p-3 flex flex-col gap-1">
                  <div className="flex justify-between gap-2 flex-wrap"><b>{v.year} {v.make} {v.model}</b><span className="mono text-xs dim">{v.vin}</span></div>
                  <InfoRow label="Mileage" value={<Distance value={v.odometer} />} />
                  {v.next_service && (
                    <InfoRow label="Next service"
                             value={<>{v.next_service.overdue ? <Badge tone="red">Overdue</Badge> : null}{" "}
                               <DateTime value={v.next_service.due_by} mode="date" /> · <Distance value={v.next_service.next_service_mileage} /></>} />
                  )}
                  <InfoRow label="Recalls" value={v.recalls?.length
                    ? <Badge tone="red">{v.recalls.length} open — book service</Badge>
                    : "None reported"} />
                  <InfoRow label="Service history"
                           value={v.service_visits
                             ? <span className="text-sm">{v.service_visits} visit{v.service_visits === 1 ? "" : "s"} on record · <Link href="/garage/service" className="underline">see history</Link></span>
                             : <span className="dim text-sm">No visits yet</span>} />
                  {(isEv(v.powertrain) || v.charger_install) && (
                    <InfoRow label="Home charging" value={v.charger_install?.status
                      ? `Charger install: ${String(v.charger_install.status).replace(/_/g, " ")}`
                      : <span className="text-sm">Not set up yet — <Link href="/shop/chat" className="underline">ask about a home charger install</Link></span>} />
                  )}
                  {(v.service_offers || []).map((o: any) => (
                    <div key={o.part} className="ai-insight m-0 mt-1" role="note">
                      <div className="ai-insight-title">🔮 Heads-up from your service team — {o.part}</div>
                      <p className="m-0 text-sm">{o.message}{" "}
                        {o.expires_at && <span className="dim">Offer valid until <DateTime value={o.expires_at} mode="date" />.</span>}</p>
                      <div className="mt-2">
                        {o.booked
                          ? <Badge tone="green">Booked for <DateTime value={o.booked.slot_start} /> · {o.offer_pct}% off applied</Badge>
                          : <Link href="/garage/service" className="btn btn-primary btn-sm">Book with {o.offer_pct}% off</Link>}
                      </div>
                    </div>
                  ))}
                  <div className="mt-1 flex gap-2 flex-wrap">
                    <Link href="/garage/service" className="btn btn-primary btn-sm">Book service</Link>
                  </div>
                </div>
              ))}
          </div>
        )}
      </Async>
    </Card>
  );
}
