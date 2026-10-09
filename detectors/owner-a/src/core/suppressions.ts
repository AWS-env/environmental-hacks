export interface SuppressionCheck {
  isSuppressed: boolean;
  reason?: string;
}

export function isLineSuppressed(
  lineContent: string,
  ruleCodes: string[] = ["F401", "CODE-C1.1", "CODE-C3.2"]
): SuppressionCheck {
  const commentIndex = lineContent.indexOf("#");
  if (commentIndex === -1) {
    return { isSuppressed: false };
  }

  const comment = lineContent.slice(commentIndex).trim();

  // Check bare # noqa
  if (/^#\s*noqa\b/i.test(comment)) {
    // Check if noqa specifies specific rules, e.g. # noqa: F401 or # noqa: CODE-C3.2
    // (dotted taxonomy codes such as CODE-C3.1 are supported too)
    const noqaMatch = comment.match(/^#\s*noqa:\s*([A-Za-z0-9_.,\s-]+)/i);
    if (!noqaMatch) {
      // Blanket # noqa suppresses everything
      return { isSuppressed: true, reason: "blanket noqa" };
    }

    const specifiedCodes = noqaMatch[1]
      .split(",")
      .map((c) => c.trim().toUpperCase());

    for (const code of ruleCodes) {
      if (specifiedCodes.includes(code.toUpperCase())) {
        return { isSuppressed: true, reason: `noqa: ${code}` };
      }
    }
    // If specific codes given and none match our rules, not suppressed
    return { isSuppressed: false };
  }

  // Check dead code suppressions
  if (/#\s*pragma:\s*no\s*cover/i.test(comment)) {
    return { isSuppressed: true, reason: "pragma: no cover" };
  }

  return { isSuppressed: false };
}
