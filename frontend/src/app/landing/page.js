/**
 * frontend/src/app/landing/page.js
 *
 * A title card, and deliberately nothing more.
 *
 * WHAT CHANGED IN THIS VERSION
 * ----------------------------
 * 1. NARROWER WORDMARK. tracking (letter-spacing) went from 0.12em to 0.02em and
 *    the size ceiling from 84px to 78px. Those are the only two dials that
 *    control the width - see the comment on the <h1> for how to nudge either.
 * 2. THE LETTERS NOW ARRIVE ONE AT A TIME. "APEX-COMMERCE" is split into 13
 *    characters, each in its own <span>, and each span's animation starts a
 *    little later than the one before it - so A appears, then P, then E, and so
 *    on. The label, hairline, summary and link then fade up after the last
 *    letter has landed.
 * 3. SHORTER SUMMARY. Two paragraphs became one sentence of about thirty words.
 *
 * HOW THE ANIMATION WORKS, IN PLAIN ENGLISH
 * -----------------------------------------
 * This is pure CSS, not JavaScript. Every letter starts invisible and runs the
 * same short "fade up out of a blur" animation; the only thing that differs is
 * animation-delay, which is index * STAGGER_MS. Because it is CSS, there is no
 * "use client" line, no React state and no effect hook - the page is still
 * static HTML rendered on the server, which is what lets it export `metadata`
 * and paint instantly.
 *
 * The keyframes live in the <style> tag at the bottom of this file rather than in
 * globals.css, so this route stays one self-contained file and nothing else in
 * your app is touched. The reduced-motion block at the end of that CSS turns the
 * animation off for anyone whose operating system asks for less movement - they
 * see the finished wordmark immediately instead of nothing at all.
 *
 * Screen readers read the aria-label on the <h1>, not the 13 separate letters,
 * which is why every span is aria-hidden.
 *
 * Every colour is a stock Tailwind class (black, emerald-400, zinc-300/500/600),
 * so this file does not depend on anything in tailwind.config.js.
 */

import Link from "next/link";

export const metadata = {
  title: "APEX-COMMERCE",
  description:
    "An AI negotiates the price. A deterministic enclave decides whether it may" +
    " be charged.",
};

const WORDMARK = "APEX-COMMERCE";

/** Gap between one letter appearing and the next. Raise it for a slower reveal. */
const STAGGER_MS = 85;

/** When the LAST letter starts moving. Everything below queues from here, which
 *  deliberately overlaps the tail of the wordmark by a few hundred milliseconds -
 *  a hard stop between the two feels mechanical. */
const AFTER_WORDMARK_MS = 260 + WORDMARK.length * STAGGER_MS;

export default function LandingPage() {
  return (
    <main className="flex min-h-screen flex-col items-center justify-center bg-black px-6 py-20 text-center">
      {/* A quiet label, so the name is not the only thing on a bare screen. */}
      <p
        className="apex-rise mb-7 text-[11px] font-medium uppercase tracking-[0.34em] text-zinc-600"
        style={{ animationDelay: "120ms" }}
      >
        Agentic Commerce Middleware
      </p>

      {/* THE WORDMARK. Two dials control its width:
            tracking-[0.02em]  - space between letters. 0 is tightest.
            clamp(28px,8vw,78px) - size: never under 28px, never over 78px.
          The characters are spans only so they can be revealed in sequence. */}
      <h1
        aria-label={WORDMARK}
        className="text-[clamp(28px,8vw,78px)] font-bold leading-none tracking-[0.02em] text-emerald-400"
      >
        {WORDMARK.split("").map((character, index) => (
          <span
            aria-hidden="true"
            className="apex-letter"
            key={`${character}-${index}`}
            style={{ animationDelay: `${260 + index * STAGGER_MS}ms` }}
          >
            {character}
          </span>
        ))}
      </h1>

      {/* A hairline that fades out at both ends, in place of a hard rule. */}
      <div
        className="apex-rise mt-9 h-px w-full max-w-[200px] bg-gradient-to-r from-transparent via-emerald-500/45 to-transparent"
        style={{ animationDelay: `${AFTER_WORDMARK_MS}ms` }}
      />

      <p
        className="apex-rise mt-8 max-w-[34rem] text-[15px] leading-relaxed text-zinc-400 sm:text-[16px]"
        style={{ animationDelay: `${AFTER_WORDMARK_MS + 140}ms` }}
      >
        An AI negotiates the price. A deterministic enclave &mdash; never the
        model &mdash; decides whether it may be charged: spending caps, a margin
        floor no discount can cross, and a six-stage audit trail.
      </p>

      {/* Delete this Link if you want the page to be a pure title card. */}
      <Link
        className="apex-rise mt-12 text-[12.5px] tracking-wide text-zinc-600 transition-colors hover:text-emerald-400"
        href="/"
        style={{ animationDelay: `${AFTER_WORDMARK_MS + 330}ms` }}
      >
        Open the dashboard &rarr;
      </Link>

      <style>{`
        @keyframes apexLetterIn {
          from { opacity: 0; transform: translateY(0.16em); filter: blur(5px); }
          to   { opacity: 1; transform: translateY(0);      filter: blur(0);   }
        }
        @keyframes apexRise {
          from { opacity: 0; transform: translateY(8px); }
          to   { opacity: 1; transform: translateY(0);   }
        }
        .apex-letter {
          display: inline-block;
          white-space: pre;
          opacity: 0;
          animation: apexLetterIn 420ms cubic-bezier(0.22, 1, 0.36, 1) forwards;
        }
        .apex-rise {
          opacity: 0;
          animation: apexRise 620ms cubic-bezier(0.22, 1, 0.36, 1) forwards;
        }
        @media (prefers-reduced-motion: reduce) {
          .apex-letter,
          .apex-rise {
            animation: none;
            opacity: 1;
            transform: none;
            filter: none;
          }
        }
      `}</style>
    </main>
  );
}
