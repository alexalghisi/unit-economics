"use client";

import { Plus, Trash2, X } from "lucide-react";
import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Input, Label, Select } from "@/components/ui/input";
import { compactCount, toNumber } from "@/lib/format";
import { addTier, describePrice, newPrice, removeTier } from "@/lib/pricing";
import type { Meter, PriceSpec, Tier } from "@/lib/types";

const KIND_LABEL: Record<PriceSpec["kind"], string> = {
  flat: "Flat fee",
  per_unit: "Per unit",
  tiered: "Tiered",
  credits: "Included credits",
};

const PER_OPTIONS = [
  { value: 1, label: "per unit" },
  { value: 1_000, label: "per 1K" },
  { value: 1_000_000, label: "per 1M" },
];

function Money({ label, value, onChange }: { label: string; value: string; onChange: (v: string) => void }) {
  return (
    <Label>
      {label}
      <div className="relative">
        <span className="pointer-events-none absolute top-1/2 left-2.5 -translate-y-1/2 text-sm text-muted">$</span>
        <Input value={value} inputMode="decimal" onChange={(e) => onChange(e.target.value)} className="w-full pl-6" />
      </div>
    </Label>
  );
}

function MeterSelect({ meters, value, onChange }: { meters: Meter[]; value: string; onChange: (id: string) => void }) {
  return (
    <Label>
      Meter
      <Select value={value} onChange={(e) => onChange(e.target.value)}>
        {meters.map((m) => (
          <option key={m.id} value={m.id}>
            {m.name}
          </option>
        ))}
      </Select>
    </Label>
  );
}

function PerSelect({ value, onChange }: { value: number; onChange: (per: number) => void }) {
  return (
    <Label>
      Unit
      <Select value={value} onChange={(e) => onChange(Number(e.target.value))}>
        {PER_OPTIONS.map((o) => (
          <option key={o.value} value={o.value}>
            {o.label}
          </option>
        ))}
      </Select>
    </Label>
  );
}

function TierRows({ tiers, onChange }: { tiers: Tier[]; onChange: (tiers: Tier[]) => void }) {
  const set = (i: number, patch: Partial<Tier>) => onChange(tiers.map((t, j) => (j === i ? { ...t, ...patch } : t)));
  return (
    <div className="col-span-full space-y-2">
      <div className="grid grid-cols-[1fr_1fr_1fr_2rem] gap-2 text-xs font-medium text-ink-2">
        <span>Up to (units)</span>
        <span>Unit amount</span>
        <span>Flat amount</span>
      </div>
      {tiers.map((t, i) => (
        <div key={i} className="grid grid-cols-[1fr_1fr_1fr_2rem] items-center gap-2">
          {t.up_to === null ? (
            <span className="text-sm text-muted">and above</span>
          ) : (
            <Input aria-label={`Tier ${i + 1} upper bound`} value={t.up_to} onChange={(e) => set(i, { up_to: e.target.value })} />
          )}
          <Input aria-label={`Tier ${i + 1} unit amount`} value={t.unit_amount} onChange={(e) => set(i, { unit_amount: e.target.value })} />
          <Input aria-label={`Tier ${i + 1} flat amount`} value={t.flat_amount} onChange={(e) => set(i, { flat_amount: e.target.value })} />
          {t.up_to !== null ? (
            <Button variant="ghost" size="icon" aria-label={`Remove tier ${i + 1}`} onClick={() => onChange(removeTier(tiers, i))}>
              <X size={14} />
            </Button>
          ) : (
            <span />
          )}
        </div>
      ))}
      <Button variant="outline" size="sm" onClick={() => onChange(addTier(tiers))}>
        <Plus size={14} /> Add tier
      </Button>
    </div>
  );
}

function PriceFields({ price, meters, onChange }: { price: PriceSpec; meters: Meter[]; onChange: (p: PriceSpec) => void }) {
  switch (price.kind) {
    case "flat":
      return <Money label="Amount per month" value={price.amount} onChange={(amount) => onChange({ ...price, amount })} />;
    case "per_unit":
      return (
        <>
          <MeterSelect meters={meters} value={price.meter_id} onChange={(meter_id) => onChange({ ...price, meter_id })} />
          <Money label="Unit amount" value={price.unit_amount} onChange={(unit_amount) => onChange({ ...price, unit_amount })} />
          <PerSelect value={price.per} onChange={(per) => onChange({ ...price, per })} />
        </>
      );
    case "credits":
      return (
        <>
          <MeterSelect meters={meters} value={price.meter_id} onChange={(meter_id) => onChange({ ...price, meter_id })} />
          <Money label="Prepaid amount" value={price.amount} onChange={(amount) => onChange({ ...price, amount })} />
          <Label>
            Included units
            <Input value={price.included_units} inputMode="numeric" onChange={(e) => onChange({ ...price, included_units: e.target.value })} />
            <span className="font-normal text-muted">{compactCount(toNumber(price.included_units))} units</span>
          </Label>
          <Money
            label="Overage amount"
            value={price.overage_unit_amount}
            onChange={(overage_unit_amount) => onChange({ ...price, overage_unit_amount })}
          />
          <PerSelect value={price.per} onChange={(per) => onChange({ ...price, per })} />
        </>
      );
    case "tiered":
      return (
        <>
          <MeterSelect meters={meters} value={price.meter_id} onChange={(meter_id) => onChange({ ...price, meter_id })} />
          <Label>
            Mode
            <Select value={price.mode} onChange={(e) => onChange({ ...price, mode: e.target.value as "graduated" | "volume" })}>
              <option value="graduated">Graduated</option>
              <option value="volume">Volume</option>
            </Select>
          </Label>
          <PerSelect value={price.per} onChange={(per) => onChange({ ...price, per })} />
          <TierRows tiers={price.tiers} onChange={(tiers) => onChange({ ...price, tiers })} />
        </>
      );
  }
}

interface Props {
  prices: PriceSpec[];
  meters: Meter[];
  onChange: (prices: PriceSpec[]) => void;
}

export function PriceEditor({ prices, meters, onChange }: Props) {
  const [kind, setKind] = useState<PriceSpec["kind"]>("per_unit");
  const defaultMeter = meters[0]?.id ?? "";
  return (
    <div className="space-y-3">
      {prices.map((price, i) => (
        <div key={i} className="rounded-lg border border-border bg-surface p-4" data-testid={`price-${i}`}>
          <div className="mb-3 flex items-start justify-between gap-2">
            <div>
              <div className="text-sm font-medium">{KIND_LABEL[price.kind]}</div>
              <div className="text-xs text-ink-2">{describePrice(price, meters)}</div>
            </div>
            <Button
              variant="ghost"
              size="icon"
              aria-label={`Remove price ${i + 1}`}
              onClick={() => onChange(prices.filter((_, j) => j !== i))}
            >
              <Trash2 size={14} />
            </Button>
          </div>
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">
            <PriceFields price={price} meters={meters} onChange={(p) => onChange(prices.map((q, j) => (j === i ? p : q)))} />
          </div>
        </div>
      ))}
      <div className="flex items-center gap-2">
        <Select aria-label="Price type to add" value={kind} onChange={(e) => setKind(e.target.value as PriceSpec["kind"])}>
          {Object.entries(KIND_LABEL).map(([k, label]) => (
            <option key={k} value={k}>
              {label}
            </option>
          ))}
        </Select>
        <Button variant="outline" onClick={() => onChange([...prices, newPrice(kind, defaultMeter)])}>
          <Plus size={14} /> Add price
        </Button>
      </div>
    </div>
  );
}
