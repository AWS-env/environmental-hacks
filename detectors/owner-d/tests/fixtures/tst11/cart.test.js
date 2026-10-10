// Synthetic TST-11 fixture: unsupported language. Never executed.
beforeEach(() => { db = new Database(); mailer = new Mailer(); });
test("total", () => { expect(total(db)).toBe(0); });
