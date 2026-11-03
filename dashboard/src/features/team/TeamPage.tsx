import { useState, type FormEvent } from "react";
import { Confirm, Modal } from "../../components/Confirm";
import { Chip, Empty, ErrorNote, PageHeader, Section } from "../../components/ui";
import { ago, dateTime, words } from "../../lib/format";
import { describeError, teamActions, useAgents, useAudit, useWorkstations } from "../../lib/queries";
import { useQueryClient } from "@tanstack/react-query";
import type { Profile } from "../../lib/types";

type Pending =
  | { kind: "reset"; agent: Profile }
  | { kind: "toggle"; agent: Profile }
  | { kind: "biometrics"; agent: Profile };

interface Secret {
  title: string;
  lines: { label: string; value: string }[];
  note: string;
}

function SecretModal({ secret, onClose }: { secret: Secret; onClose: () => void }) {
  const [copied, setCopied] = useState<string | null>(null);
  const copy = async (v: string) => {
    try {
      await navigator.clipboard.writeText(v);
      setCopied(v);
    } catch {
      /* the value is on screen: it can be copied by hand */
    }
  };
  return (
    <Modal title={secret.title} onClose={onClose}>
      <div className="space-y-3">
        {secret.lines.map((l) => (
          <div key={l.label}>
            <p className="label">{l.label}</p>
            <div className="mt-1 flex items-center gap-2">
              <code className="input select-all !bg-g100">{l.value}</code>
              <button className="btn" onClick={() => void copy(l.value)}>{copied === l.value ? "copied" : "copy"}</button>
            </div>
          </div>
        ))}
      </div>
      <p className="mt-4 text-xs text-g500">{secret.note}</p>
      <button className="btn btn-primary mt-5" onClick={onClose}>done</button>
    </Modal>
  );
}

export function TeamPage() {
  const qc = useQueryClient();
  const agents = useAgents();
  const workstations = useWorkstations();
  const audit = useAudit(60);
  const [secret, setSecret] = useState<Secret | null>(null);
  const [pending, setPending] = useState<Pending | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");

  const run = async (fn: () => Promise<void>) => {
    setBusy(true);
    setError(null);
    try {
      await fn();
    } catch (e) {
      setError(await describeError(e));
    } finally {
      setBusy(false);
    }
  };
  const refresh = () => {
    for (const k of ["agents", "audit", "agent_status"]) void qc.invalidateQueries({ queryKey: [k] });
  };

  const create = (e: FormEvent) => {
    e.preventDefault();
    void run(async () => {
      const made = await teamActions.createAgent(email.trim(), name.trim());
      setSecret({
        title: "Account created",
        lines: [{ label: "email", value: made.email }, { label: "temporary password", value: made.temp_password }],
        note: "This password is shown only once. Give it to the agent; they must choose their own at the first sign-in.",
      });
      setName("");
      setEmail("");
      refresh();
    });
  };

  const confirm = () => {
    if (!pending) return;
    const { agent } = pending;
    void run(async () => {
      if (pending.kind === "reset") {
        const r = await teamActions.resetPassword(agent.id);
        setSecret({ title: `New password for ${agent.full_name}`, lines: [{ label: "temporary password", value: r.temp_password }], note: "Shown only once. Their sign-in and face check were ended, and they must choose a new password." });
      } else if (pending.kind === "toggle") {
        await teamActions.setActive(agent.id, !agent.active);
      } else {
        await teamActions.deleteBiometrics(agent.id);
      }
      setPending(null);
      refresh();
    });
  };

  const code = (label: string, fn: () => Promise<{ code: string }>, title: string, note: string) =>
    void run(async () => {
      const r = await fn();
      setSecret({ title, lines: [{ label, value: r.code }], note });
      refresh();
    });

  return (
    <>
      <PageHeader
        title="team"
        kicker="accounts, codes and the audit trail"
        right={<button className="btn" disabled={busy} onClick={() => code("pairing code", teamActions.pairingCode, "Pairing code", "Valid for 15 minutes, works once. The agent enters it on the call desk app to link their PC to its webcam.")}>new pairing code</button>}
      />
      {error && <div className="mb-6"><ErrorNote error={new Error(error)} /></div>}

      <Section n={1} title="agents">
        {(agents.data ?? []).length === 0 ? (
          <Empty>No agents yet. Create the first one below.</Empty>
        ) : (
          <div className="card overflow-x-auto">
            <table className="w-full text-left text-sm">
              <thead><tr>{["agent", "account", "face", "actions"].map((h) => <th key={h} className="label p-3 font-normal">{h}</th>)}</tr></thead>
              <tbody>
                {(agents.data ?? []).map((a) => (
                  <tr key={a.id} className="table-row align-top">
                    <td className="p-3 font-medium">{a.full_name}</td>
                    <td className="p-3">{a.active ? <Chip>active</Chip> : <Chip tone="ink">deactivated</Chip>}{a.must_change_password && <span className="label ml-2">temporary password</span>}</td>
                    <td className="p-3">{a.consent_at ? <Chip>enrolled</Chip> : <span className="text-xs text-g500">not enrolled</span>}</td>
                    <td className="p-3">
                      <div className="flex flex-wrap gap-2">
                        <button className="btn !py-1" disabled={busy || !a.active} onClick={() => code("enrollment code", () => teamActions.enrollmentCode(a.id), `Enrollment code for ${a.full_name}`, "Valid for 15 minutes, works once. With it the agent gives consent and saves their face; without it a stolen password cannot enroll someone else.")}>enrollment code</button>
                        <button className="btn !py-1" disabled={busy} onClick={() => setPending({ kind: "reset", agent: a })}>reset password</button>
                        <button className="btn !py-1" disabled={busy} onClick={() => setPending({ kind: "toggle", agent: a })}>{a.active ? "deactivate" : "activate"}</button>
                        <button className="btn !py-1" disabled={busy || !a.consent_at} onClick={() => setPending({ kind: "biometrics", agent: a })}>delete face data</button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        <form onSubmit={create} className="card mt-4 flex flex-wrap items-end gap-3 p-5">
          <label className="block min-w-[12rem] flex-1">
            <span className="label">full name</span>
            <input className="input mt-1" value={name} onChange={(e) => setName(e.target.value)} required maxLength={120} />
          </label>
          <label className="block min-w-[14rem] flex-1">
            <span className="label">email (their sign-in name)</span>
            <input className="input mt-1" type="email" value={email} onChange={(e) => setEmail(e.target.value)} required />
          </label>
          <button className="btn btn-primary" disabled={busy || !name.trim() || !email.trim()}>create agent</button>
        </form>
        <p className="label mt-3">no email is sent: you hand the temporary password to the agent yourself</p>
      </Section>

      <Section n={2} title="paired PCs" index={2}>
        {(workstations.data ?? []).length === 0 ? (
          <Empty>No PC is paired yet.</Empty>
        ) : (
          <div className="card overflow-x-auto">
            <table className="w-full text-left text-sm">
              <thead><tr>{["name", "paired", "last seen", ""].map((h) => <th key={h} className="label p-3 font-normal">{h}</th>)}</tr></thead>
              <tbody>
                {(workstations.data ?? []).map((w) => (
                  <tr key={w.id} className="table-row">
                    <td className="p-3">{w.name}</td>
                    <td className="p-3 font-mono text-xs">{dateTime(w.created_at)}</td>
                    <td className="p-3 label">{ago(w.last_seen_at)}</td>
                    <td className="p-3">{w.revoked_at && <Chip tone="ink">revoked</Chip>}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Section>

      <Section n={3} title="audit trail" index={3} right={<span className="label">latest 60</span>}>
        {(audit.data ?? []).length === 0 ? (
          <Empty>Nothing recorded yet.</Empty>
        ) : (
          <ul className="card divide-y">
            {(audit.data ?? []).map((r) => {
              const who = (agents.data ?? []).find((a) => a.id === r.target_id)?.full_name;
              return (
                <li key={r.id} className="flex flex-wrap items-center justify-between gap-3 px-4 py-3 text-sm">
                  <span>
                    {words(r.action)}
                    {who && <span className="text-g500"> · {who}</span>}
                  </span>
                  <span className="label">{dateTime(r.ts)}</span>
                </li>
              );
            })}
          </ul>
        )}
      </Section>

      {secret && <SecretModal secret={secret} onClose={() => setSecret(null)} />}
      {pending?.kind === "reset" && (
        <Confirm title="Reset password?" confirmLabel="reset password" busy={busy} onConfirm={confirm} onClose={() => setPending(null)}>
          {pending.agent.full_name} will be signed out and must choose a new password. Their current face check ends too.
        </Confirm>
      )}
      {pending?.kind === "toggle" && (
        <Confirm title={pending.agent.active ? "Deactivate this account?" : "Activate this account?"} confirmLabel={pending.agent.active ? "deactivate" : "activate"} danger={pending.agent.active} busy={busy} onConfirm={confirm} onClose={() => setPending(null)}>
          {pending.agent.active
            ? `${pending.agent.full_name} will be signed out and cannot sign in until you activate the account again.`
            : `${pending.agent.full_name} will be able to sign in again.`}
        </Confirm>
      )}
      {pending?.kind === "biometrics" && (
        <Confirm title="Delete face data?" confirmLabel="delete face data" danger busy={busy} onConfirm={confirm} onClose={() => setPending(null)}>
          The saved face template and calibration of {pending.agent.full_name} are deleted for good, and their consent is cleared. They must enroll again to use the face check.
        </Confirm>
      )}
    </>
  );
}
