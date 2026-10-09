/**
 * Label that rolls up to a clone on hover (lusion's `text` / `text-clone` pair).
 * The parent must have the `group` class. Pure CSS, so it needs no JavaScript.
 */
export default function RollText({ children }: { children: string }) {
  const roll =
    "block transition-transform duration-500 ease-[cubic-bezier(0.7,0,0.2,1)] motion-reduce:transition-none";
  return (
    <span className="relative inline-flex overflow-hidden">
      <span className={`${roll} group-hover:-translate-y-full motion-reduce:group-hover:translate-y-0`}>{children}</span>
      <span
        aria-hidden="true"
        className={`${roll} absolute inset-0 translate-y-full group-hover:translate-y-0 motion-reduce:hidden`}
      >
        {children}
      </span>
    </span>
  );
}
