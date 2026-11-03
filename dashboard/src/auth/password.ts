/** The project's password rules (the same ones Supabase enforces): 12+ characters with upper case, lower case and a digit. */
export function passwordProblems(password: string): string[] {
  const out: string[] = [];
  if (password.length < 12) out.push("at least 12 characters");
  if (!/[a-z]/.test(password)) out.push("a lower-case letter");
  if (!/[A-Z]/.test(password)) out.push("an upper-case letter");
  if (!/[0-9]/.test(password)) out.push("a digit");
  return out;
}
