// Builds the caller's system prompt. The TypeScript twin of simulation/callers.py: a golden-file
// test (tests/caller.test.ts) keeps both producing exactly the same text.
import { BASE_PROMPT, LANGUAGE_STYLES, MOODS } from "./prompts.generated.ts";

export interface ScenarioRow {
  customer: string;
  brief: string;
  facts: Record<string, unknown>;
}

export interface CustomerRow {
  full_name: string;
  account_no: string;
  plan: string;
  balance: number | string;
  profile: Record<string, unknown> | null;
}

const PLACEHOLDER = /\{([a-z_]+)\}/g;

export function customerValues(c: CustomerRow): Record<string, string> {
  const values: Record<string, string> = {
    full_name: c.full_name,
    account_no: c.account_no,
    plan: c.plan,
    balance: Number(c.balance).toFixed(2),
  };
  for (const [k, v] of Object.entries(c.profile ?? {})) values[k] = String(v);
  return values;
}

export function fill(text: string, values: Record<string, string>): string {
  return text.replace(PLACEHOLDER, (m, key: string) => values[key] ?? m);
}

export function buildSystemPrompt(scenario: ScenarioRow, customer: CustomerRow, mood: string, language: string): string {
  const style = LANGUAGE_STYLES[language];
  const moodText = MOODS[mood];
  if (style === undefined) throw new Error(`unknown language: ${language}`);
  if (moodText === undefined) throw new Error(`unknown mood: ${mood}`);
  const values = customerValues(customer);
  const facts = Object.entries(scenario.facts)
    .map(([k, v]) => `- ${k.replaceAll("_", " ")}: ${fill(String(v), values)}`)
    .join("\n");
  // function replacers: a plain string would treat "$&" and friends in the text as patterns
  return BASE_PROMPT
    .replace("{customer}", () => fill(scenario.customer, values))
    .replace("{brief}", () => fill(scenario.brief, values))
    .replace("{facts}", () => facts)
    .replace("{mood}", () => moodText)
    .replace("{language_style}", () => style);
}
